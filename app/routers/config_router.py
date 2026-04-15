"""
ConfigRouter
GET /config | PUT /config | POST /config/reset
"""

from fastapi import APIRouter

from app.models.schemas import ConfigUpdateRequest, ScoringConfig

router = APIRouter(prefix="/config", tags=["Config"])


@router.get("")
async def get_config():
    """스코어링 설정 조회"""
    from app.main import app_state
    row = await app_state.repo.get_config()
    return row or {}


@router.put("")
async def update_config(req: ConfigUpdateRequest):
    """설정 부분 업데이트"""
    from app.main import app_state
    update_data = req.model_dump(exclude_none=True)
    changed_by = update_data.pop("updated_by", "admin")

    if update_data:
        await app_state.repo.update_config(update_data, changed_by=changed_by)

        # 캐시 갱신
        new_config = await app_state.repo.get_config()
        if new_config:
            cfg = ScoringConfig(**{k: v for k, v in new_config.items() if k in ScoringConfig.model_fields})
            app_state.scorer.reload_config(cfg)
            app_state.risk_evaluator.reload_config(cfg)
            app_state.alert_manager.min_interval_sec = cfg.alert_min_interval_sec

    return await app_state.repo.get_config()


@router.post("/reset")
async def reset_config():
    """설정 기본값 리셋"""
    from app.main import app_state
    result = await app_state.repo.reset_config()

    # 캐시 갱신
    if result:
        cfg = ScoringConfig(**{k: v for k, v in result.items() if k in ScoringConfig.model_fields})
        app_state.scorer.reload_config(cfg)
        app_state.risk_evaluator.reload_config(cfg)
        app_state.alert_manager.min_interval_sec = cfg.alert_min_interval_sec

    return result
