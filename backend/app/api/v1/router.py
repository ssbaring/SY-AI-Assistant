"""/api/v1 공통 라우터 집합점.

새 리소스가 추가되면 여기에 include_router 한 줄만 더한다. 그 이상의
동적 등록기나 범용 프레임워크는 만들지 않는다.
"""

from fastapi import APIRouter

from app.api.v1 import weighing_records

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(weighing_records.router)
