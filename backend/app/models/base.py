from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 제약조건 이름을 고정해 두면 Alembic 마이그레이션에서 어떤 제약을 바꾸는지
# 이름만 보고 알 수 있고, downgrade에서도 정확히 drop할 수 있다.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
