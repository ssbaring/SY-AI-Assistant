"""차량번호 정규화 규칙.

weighing_records.vehicle_no_norm 컬럼 및 그 CHECK 제약
(vehicle_no_norm !~ '[[:space:]-]')과 같은 규칙을 따른다: 공백과 하이픈을
제거하고, 영문자는 대문자로 통일한다. 이 함수는 조회 필터(vehicle_number)와
DB에 저장된 vehicle_no_norm이 같은 방식으로 정규화되어 있다는 전제로 완전일치
비교에 쓰인다.
"""

import re

_STRIP_PATTERN = re.compile(r"[\s\-]")


def normalize_vehicle_no(raw: str) -> str:
    return _STRIP_PATTERN.sub("", raw).upper()
