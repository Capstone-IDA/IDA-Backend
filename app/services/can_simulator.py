"""
CANSimulator
시나리오 프리셋 기반 속도/가속도/브레이크 데이터 생성
"""

import asyncio
import logging
import math
import random
from collections import deque
from datetime import datetime
from typing import Optional

from app.models.schemas import CANSnapshot, ScenarioPreset

logger = logging.getLogger(__name__)


# 시나리오 프리셋 정의
DEFAULT_SCENARIOS: dict[str, ScenarioPreset] = {
    "normal": ScenarioPreset(
        name="normal",
        description="정상 주행: 완만한 가감속, 제한속도 준수",
        speed_profile=[0, 5, 10, 15, 20, 20, 15, 10, 5, 0],
        accel_profile=[0.5, 0.5, 0.5, 0.3, 0.0, -0.3, -0.5, -0.5, -0.5, 0.0],
        brake_profile=[0.0, 0.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.2, 0.3, 0.0],
        duration_sec=60,
    ),
    "sudden_start": ScenarioPreset(
        name="sudden_start",
        description="급출발 패턴: 정지 상태에서 가속도 급상승",
        speed_profile=[0, 0, 5, 20, 35, 40, 35, 25, 15, 0],
        accel_profile=[0.0, 0.5, 3.5, 4.0, 2.5, 0.0, -1.0, -1.5, -1.0, 0.0],
        brake_profile=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2, 0.3, 0.4, 0.0],
        duration_sec=45,
    ),
    "sudden_brake": ScenarioPreset(
        name="sudden_brake",
        description="급제동 패턴: 주행 중 감속도 급상승",
        speed_profile=[0, 10, 20, 30, 30, 25, 10, 2, 0, 0],
        accel_profile=[1.0, 1.0, 1.0, 0.5, 0.0, -3.0, -4.5, -3.0, 0.0, 0.0],
        brake_profile=[0.0, 0.0, 0.0, 0.0, 0.0, 0.6, 0.9, 0.8, 0.3, 0.0],
        duration_sec=45,
    ),
    "complex_danger": ScenarioPreset(
        name="complex_danger",
        description="복합 위험: 근접 객체 감지 상태에서 급가속/급제동 반복",
        speed_profile=[0, 5, 25, 35, 10, 5, 30, 40, 5, 0],
        accel_profile=[0.5, 3.0, 3.5, 1.0, -4.0, -1.0, 3.5, 2.0, -4.5, 0.0],
        brake_profile=[0.0, 0.0, 0.0, 0.0, 0.8, 0.2, 0.0, 0.0, 0.9, 0.0],
        duration_sec=60,
    ),
    "overspeeding": ScenarioPreset(
        name="overspeeding",
        description="과속 패턴: 제한속도 초과 주행",
        speed_profile=[0, 10, 25, 35, 45, 50, 45, 35, 20, 0],
        accel_profile=[1.0, 1.5, 1.5, 1.0, 0.5, 0.0, -1.0, -1.5, -2.0, 0.0],
        brake_profile=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2, 0.3, 0.5, 0.0],
        duration_sec=50,
    ),
}


class CANSimulator:
    """CAN 시뮬레이터: 시나리오 프리셋 기반 가상 차량 데이터 생성"""

    def __init__(self):
        self.scenarios: dict[str, ScenarioPreset] = dict(DEFAULT_SCENARIOS)
        self.current_scenario: str = "normal"
        self.is_running: bool = False
        self.generation_interval: float = 0.1  # 100ms 주기
        self.buffer: deque[CANSnapshot] = deque(maxlen=1000)
        self._task: Optional[asyncio.Task] = None
        self._step: int = 0

    def load_scenario(self, name: str) -> None:
        """시나리오 로드"""
        if name not in self.scenarios:
            raise ValueError(f"알 수 없는 시나리오: {name}. 가능: {list(self.scenarios.keys())}")
        self.current_scenario = name
        self._step = 0
        logger.info(f"시나리오 로드: {name}")

    def start(self) -> None:
        """시뮬레이션 시작 (비동기 태스크)"""
        if self.is_running:
            return
        self.is_running = True
        self._step = 0
        self._task = asyncio.create_task(self._generate_loop())
        logger.info(f"CAN 시뮬레이터 시작: {self.current_scenario}")

    def stop(self) -> None:
        """시뮬레이션 중지"""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        logger.info("CAN 시뮬레이터 중지")

    async def _generate_loop(self) -> None:
        """비동기 데이터 생성 루프"""
        try:
            while self.is_running:
                snapshot = self.generate_data()
                self.buffer.append(snapshot)
                await asyncio.sleep(self.generation_interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"CAN 생성 루프 에러: {e}")

    def generate_data(self) -> CANSnapshot:
        """현재 시나리오 기반 CAN 데이터 1건 생성"""
        preset = self.scenarios[self.current_scenario]
        profile_len = len(preset.speed_profile)

        # 프로필 보간: 현재 스텝을 프로필 인덱스로 매핑
        total_steps = int(preset.duration_sec / self.generation_interval)
        progress = (self._step % total_steps) / total_steps
        idx_float = progress * (profile_len - 1)
        idx = int(idx_float)
        frac = idx_float - idx

        # 선형 보간 + 노이즈
        def interp(profile: list[float]) -> float:
            if idx >= len(profile) - 1:
                return profile[-1]
            base = profile[idx] + (profile[idx + 1] - profile[idx]) * frac
            noise = random.gauss(0, 0.1)
            return base + noise

        speed = max(0, interp(preset.speed_profile))
        accel = interp(preset.accel_profile)
        brake = max(0, min(1, interp(preset.brake_profile)))

        self._step += 1

        return CANSnapshot(
            timestamp=datetime.utcnow(),
            speed_kmh=round(speed, 2),
            acceleration=round(accel, 3),
            brake_intensity=round(brake, 3),
            scenario=self.current_scenario,
        )

    def get_latest(self) -> Optional[CANSnapshot]:
        """최신 CAN 데이터 반환"""
        if self.buffer:
            return self.buffer[-1]
        return None

    def list_scenarios(self) -> list[str]:
        """사용 가능한 시나리오 목록"""
        return list(self.scenarios.keys())
