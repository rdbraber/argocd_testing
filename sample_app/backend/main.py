from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, ForeignKey, UniqueConstraint, Boolean, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from datetime import datetime, date, timedelta
from typing import List, Optional
import bcrypt
import jwt
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://scheduler:scheduler@postgres:5432/scheduler")
JWT_SECRET = os.getenv("JWT_SECRET", "changeme-in-production")
JWT_ALGORITHM = "HS256"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

app = FastAPI(title="Volunteer Scheduler")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
security = HTTPBearer()

SLOT_TEMPLATES = [
    {"start_time": "09:00", "end_time": "10:30", "required_count": 1, "label": "Driver"},
    {"start_time": "10:00", "end_time": "13:00", "required_count": 2, "label": "Morning shift"},
    {"start_time": "13:00", "end_time": "16:00", "required_count": 2, "label": "Afternoon shift"},
    {"start_time": "15:30", "end_time": "17:00", "required_count": 2, "label": "Driver & Helper"},
]


# --- Models ---

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime, default=datetime.utcnow)
    slot_signups = relationship("SlotSignup", back_populates="user", cascade="all, delete-orphan")


class Schedule(Base):
    __tablename__ = "schedules"
    id = Column(Integer, primary_key=True)
    date = Column(Date, unique=True, nullable=False)
    slots = relationship("TimeSlot", back_populates="schedule", order_by="TimeSlot.start_time")


class TimeSlot(Base):
    __tablename__ = "time_slots"
    id = Column(Integer, primary_key=True)
    schedule_id = Column(Integer, ForeignKey("schedules.id"), nullable=False)
    start_time = Column(String(5), nullable=False)
    end_time = Column(String(5), nullable=False)
    required_count = Column(Integer, nullable=False)
    label = Column(String(100), nullable=False)
    schedule = relationship("Schedule", back_populates="slots")
    signups = relationship("SlotSignup", back_populates="slot", cascade="all, delete-orphan")


class SlotSignup(Base):
    __tablename__ = "slot_signups"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    slot_id = Column(Integer, ForeignKey("time_slots.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="slot_signups")
    slot = relationship("TimeSlot", back_populates="signups")
    __table_args__ = (UniqueConstraint("user_id", "slot_id"),)


# --- Schemas ---

class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    is_admin: bool

    class Config:
        from_attributes = True


class AdminCreateUserRequest(BaseModel):
    name: str
    email: str
    password: str
    is_admin: bool = False


class AdminUpdateUserRequest(BaseModel):
    name: str
    email: str
    password: Optional[str] = None
    is_admin: bool = False


class SlotOut(BaseModel):
    id: int
    start_time: str
    end_time: str
    label: str
    required_count: int
    signup_count: int
    signed_up: bool
    volunteers: List[str]
    volunteer_ids: List[int]
    full: bool

    class Config:
        from_attributes = True


class ScheduleOut(BaseModel):
    id: int
    date: date
    slots: List[SlotOut]

    class Config:
        from_attributes = True


# --- Helpers ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: int) -> str:
    payload = {"sub": str(user_id), "exp": datetime.utcnow() + timedelta(hours=24)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def get_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


def seed_admin(db: Session):
    if not db.query(User).filter(User.email == "admin@scheduler.local").first():
        db.add(User(
            name="Admin",
            email="admin@scheduler.local",
            password_hash=hash_password("admin"),
            is_admin=True,
        ))
        db.commit()


def seed_schedules(db: Session):
    if db.query(Schedule).count() == 0:
        today = date.today()
        days_ahead = (5 - today.weekday()) % 7 or 7
        next_saturday = today + timedelta(days=days_ahead)
        for i in range(16):
            db.add(Schedule(date=next_saturday + timedelta(weeks=i)))
        db.commit()

    for schedule in db.query(Schedule).all():
        if not schedule.slots:
            for tmpl in SLOT_TEMPLATES:
                db.add(TimeSlot(schedule_id=schedule.id, **tmpl))
    db.commit()


# --- Startup ---

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
            "is_admin BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        conn.commit()
    db = SessionLocal()
    try:
        seed_admin(db)
        seed_schedules(db)
    finally:
        db.close()


# --- Routes ---

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/auth/register", status_code=201)
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(name=data.name, email=data.email, password_hash=hash_password(data.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"token": create_token(user.id), "name": user.name, "is_admin": user.is_admin}


@app.post("/api/auth/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"token": create_token(user.id), "name": user.name, "is_admin": user.is_admin}


@app.get("/api/me")
def me(current_user: User = Depends(get_current_user)):
    return {"id": current_user.id, "name": current_user.name,
            "email": current_user.email, "is_admin": current_user.is_admin}


@app.get("/api/schedules", response_model=List[ScheduleOut])
def list_schedules(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    schedules = (db.query(Schedule)
                 .filter(Schedule.date >= date.today())
                 .order_by(Schedule.date)
                 .all())
    result = []
    for s in schedules:
        slots = []
        for slot in s.slots:
            signed_up = any(su.user_id == current_user.id for su in slot.signups)
            slots.append(SlotOut(
                id=slot.id,
                start_time=slot.start_time,
                end_time=slot.end_time,
                label=slot.label,
                required_count=slot.required_count,
                signup_count=len(slot.signups),
                signed_up=signed_up,
                volunteers=[su.user.name for su in slot.signups],
                volunteer_ids=[su.user_id for su in slot.signups],
                full=len(slot.signups) >= slot.required_count and not signed_up,
            ))
        result.append(ScheduleOut(id=s.id, date=s.date, slots=slots))
    return result


@app.post("/api/slots/{slot_id}/signup", status_code=201)
def signup(slot_id: int, current_user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    slot = db.query(TimeSlot).filter(TimeSlot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    if slot.schedule.date < date.today():
        raise HTTPException(status_code=400, detail="Cannot sign up for past dates")
    if len(slot.signups) >= slot.required_count:
        raise HTTPException(status_code=400, detail="This slot is already full")
    if db.query(SlotSignup).filter(SlotSignup.user_id == current_user.id,
                                    SlotSignup.slot_id == slot_id).first():
        raise HTTPException(status_code=400, detail="Already signed up for this slot")
    db.add(SlotSignup(user_id=current_user.id, slot_id=slot_id))
    db.commit()
    return {"message": "Signed up successfully"}


@app.delete("/api/slots/{slot_id}/signup")
def cancel_signup(slot_id: int, current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    su = db.query(SlotSignup).filter(SlotSignup.user_id == current_user.id,
                                      SlotSignup.slot_id == slot_id).first()
    if not su:
        raise HTTPException(status_code=404, detail="Not signed up for this slot")
    db.delete(su)
    db.commit()
    return {"message": "Cancelled successfully"}


# --- Admin: User CRUD ---

@app.get("/api/admin/users", response_model=List[UserOut])
def admin_list_users(admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    return db.query(User).order_by(User.id).all()


@app.post("/api/admin/users", response_model=UserOut, status_code=201)
def admin_create_user(data: AdminCreateUserRequest, admin: User = Depends(get_admin_user),
                      db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(name=data.name, email=data.email,
                password_hash=hash_password(data.password), is_admin=data.is_admin)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.put("/api/admin/users/{user_id}", response_model=UserOut)
def admin_update_user(user_id: int, data: AdminUpdateUserRequest,
                      admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.name = data.name
    user.email = data.email
    user.is_admin = data.is_admin
    if data.password:
        user.password_hash = hash_password(data.password)
    db.commit()
    db.refresh(user)
    return user


@app.delete("/api/admin/users/{user_id}", status_code=204)
def admin_delete_user(user_id: int, admin: User = Depends(get_admin_user),
                      db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    db.delete(user)
    db.commit()


# --- Admin: Slot assignment ---

@app.post("/api/admin/slots/{slot_id}/users/{user_id}", status_code=201)
def admin_slot_signup(slot_id: int, user_id: int, admin: User = Depends(get_admin_user),
                      db: Session = Depends(get_db)):
    if not db.query(TimeSlot).filter(TimeSlot.id == slot_id).first():
        raise HTTPException(status_code=404, detail="Slot not found")
    if not db.query(User).filter(User.id == user_id).first():
        raise HTTPException(status_code=404, detail="User not found")
    if db.query(SlotSignup).filter(SlotSignup.user_id == user_id,
                                    SlotSignup.slot_id == slot_id).first():
        raise HTTPException(status_code=400, detail="User already signed up for this slot")
    db.add(SlotSignup(user_id=user_id, slot_id=slot_id))
    db.commit()
    return {"message": "User added to slot"}


@app.delete("/api/admin/slots/{slot_id}/users/{user_id}")
def admin_slot_remove(slot_id: int, user_id: int, admin: User = Depends(get_admin_user),
                      db: Session = Depends(get_db)):
    su = db.query(SlotSignup).filter(SlotSignup.user_id == user_id,
                                      SlotSignup.slot_id == slot_id).first()
    if not su:
        raise HTTPException(status_code=404, detail="User not signed up for this slot")
    db.delete(su)
    db.commit()
    return {"message": "Removed user from slot"}
