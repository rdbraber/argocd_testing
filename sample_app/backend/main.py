from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from datetime import datetime, date, timedelta
from typing import List
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


# --- Models ---

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    signups = relationship("Signup", back_populates="user")


class Schedule(Base):
    __tablename__ = "schedules"
    id = Column(Integer, primary_key=True)
    date = Column(Date, unique=True, nullable=False)
    signups = relationship("Signup", back_populates="schedule")


class Signup(Base):
    __tablename__ = "signups"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    schedule_id = Column(Integer, ForeignKey("schedules.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="signups")
    schedule = relationship("Schedule", back_populates="signups")
    __table_args__ = (UniqueConstraint("user_id", "schedule_id"),)


# --- Schemas ---

class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class ScheduleOut(BaseModel):
    id: int
    date: date
    signup_count: int
    signed_up: bool

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


def seed_schedules(db: Session):
    if db.query(Schedule).count() > 0:
        return
    today = date.today()
    days_ahead = (5 - today.weekday()) % 7 or 7
    next_saturday = today + timedelta(days=days_ahead)
    for i in range(16):
        db.add(Schedule(date=next_saturday + timedelta(weeks=i)))
    db.commit()


# --- Startup ---

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
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
    return {"token": create_token(user.id), "name": user.name}


@app.post("/api/auth/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"token": create_token(user.id), "name": user.name}


@app.get("/api/me")
def me(current_user: User = Depends(get_current_user)):
    return {"id": current_user.id, "name": current_user.name, "email": current_user.email}


@app.get("/api/schedules", response_model=List[ScheduleOut])
def list_schedules(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    schedules = (db.query(Schedule)
                 .filter(Schedule.date >= date.today())
                 .order_by(Schedule.date)
                 .all())
    result = []
    for s in schedules:
        signed_up = any(su.user_id == current_user.id for su in s.signups)
        result.append(ScheduleOut(id=s.id, date=s.date,
                                  signup_count=len(s.signups), signed_up=signed_up))
    return result


@app.post("/api/schedules/{schedule_id}/signup", status_code=201)
def signup(schedule_id: int, current_user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if schedule.date < date.today():
        raise HTTPException(status_code=400, detail="Cannot sign up for past dates")
    if db.query(Signup).filter(Signup.user_id == current_user.id,
                                Signup.schedule_id == schedule_id).first():
        raise HTTPException(status_code=400, detail="Already signed up")
    db.add(Signup(user_id=current_user.id, schedule_id=schedule_id))
    db.commit()
    return {"message": "Signed up successfully"}


@app.delete("/api/schedules/{schedule_id}/signup")
def cancel_signup(schedule_id: int, current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    signup = db.query(Signup).filter(Signup.user_id == current_user.id,
                                      Signup.schedule_id == schedule_id).first()
    if not signup:
        raise HTTPException(status_code=404, detail="Not signed up for this date")
    db.delete(signup)
    db.commit()
    return {"message": "Cancelled successfully"}
