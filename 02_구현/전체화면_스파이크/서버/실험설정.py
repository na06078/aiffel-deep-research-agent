"""오프라인 검증 가능한 불변 실험 설정. 정상 앱은 None으로 기존 계약을 유지한다."""

import hashlib
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import MappingProxyType


def source_manifest():
    """서버 코드의 실제 바이트 지문. 자격증명/실행 자료는 읽지 않는다."""
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(__file__).parent.glob("*.py"))
    }


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    arm: str = "baseline"
    section_count: int = 4
    reads_per_section: int = 3
    max_rounds: int = 2
    single_actor: bool = False
    assignment: bool = True
    role: bool = True
    region: bool = True
    redelegation: bool = True

    def __post_init__(self):
        if not isinstance(self.arm, str) or not self.arm.strip():
            raise ValueError("experiment: arm required")
        for key, low, high in (
            ("section_count", 1, 4),
            ("reads_per_section", 1, 3),
            ("max_rounds", 1, 2),
        ):
            value = getattr(self, key)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"experiment: invalid {key}")
        for key in ("single_actor", "assignment", "role", "region", "redelegation"):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f"experiment: invalid {key}")

    @property
    def reads_per_round(self):
        return self.section_count * self.reads_per_section

    def as_dict(self):
        return {
            **asdict(self),
            "reads_per_round": self.reads_per_round,
            "max_actual_requests": 55,
            "automatic_retry": False,
            "min_visible_body_chars": 2000,
            "max_references_per_section": 16,
            "correction_attempts": 1,
            "correction_max_calls": 2,
        }


_base = ExperimentConfig()
ARMS = MappingProxyType(
    {
        "baseline": _base,
        "single": replace(_base, arm="single", single_actor=True),
        "width2": replace(_base, arm="width2", section_count=2),
        "depth1": replace(_base, arm="depth1", reads_per_section=1),
        "no-assignment": replace(_base, arm="no-assignment", assignment=False),
        "no-role": replace(_base, arm="no-role", role=False),
        "no-region": replace(_base, arm="no-region", region=False),
        "no-redelegation": replace(_base, arm="no-redelegation", redelegation=False),
    }
)

ARM_NOTES = MappingProxyType(
    {
        "baseline": "4절×3읽기, 최대2라운드의 격리 멀티 기준. 모델의 조기 읽기 종료는 실제 수치로 남긴다.",
        "single": "한 조사자·공통 읽은문서/메모·공통 원문 구간 문맥을 모든 절 작성에 사용. 1라운드 최대12고유문서. 다중 역할별 고립 문맥의 직렬 실행이 아니다. 공통 기획/종합/최종 검토 단계는 유지하고 조사·집필 주체만 하나다. 원문 전체가 아닌 동일 구간선택/크기 상한을 적용한다.",
        "width2": "폭만2절로 축소하므로 라운드 읽기 총량도12→6이다. 동일 총량 비교가 아니다.",
        "depth1": "절당 읽기만3→1이므로 라운드 총량12→4이다.",
        "no-assignment": "목차/출력 절 구조는 유지하지만 모든 조사자의 맞춤 지시·시작문서 배정을 제거하고 전체 질문을 전달한다. 추가 select 요청 가능.",
        "no-role": "기획 명단과 선택/작성의 전문 역할을 중립 역할로 대체한다.",
        "no-region": "다른 절 seed·읽은문서의 avoid 전달만 제거한다. 자기 중복 읽기 금지는 유지한다.",
        "no-redelegation": "부족절 재위임만 금지한다. 부족은 성공으로 승격하지 않는다.",
    }
)
