from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from config import AppConfig
from core.run_metadata import create_run_id
from core.research_campaign import (
    DEFAULT_CAMPAIGN_DB,
    claim_holdout,
    complete_holdout,
    record_attempt,
    register_candidate,
    resolve_campaign_db_path,
)
from core.panel_identity import DataIdentityError, panel_data_identity
from core.research_identity import (
    stable_hash,
    strategy_source_identity,
)

from .backtest_session_service import BacktestSessionOutcome, run_backtest_session
from .strategy_validation.gates import build_verdict, evaluate_gates
from .strategy_validation.models import StrategyValidationProtocol
from .strategy_validation.protocol import load_protocol, protocol_to_dict
from .strategy_validation.reporting import write_validation_report
from .strategy_validation.runner import default_output_root, run_validation_case
from .strategy_validation.trust import build_validation_preflight


class StrategyValidationService:
    """Run structured validation cases for a strategy module."""

    def __init__(
        self,
        session: Callable[..., BacktestSessionOutcome] | None = None,
    ) -> None:
        self.session = session or run_backtest_session

    @staticmethod
    def load_protocol(path: str | Path) -> StrategyValidationProtocol:
        return load_protocol(path)

    def run(
        self,
        *,
        base_cfg: AppConfig,
        module: str,
        protocol: StrategyValidationProtocol,
        output_root: str | Path | None = None,
        campaign_id: str = "",
        candidate_id: str = "",
        campaign_db: str | Path | None = None,
        promotion: bool = False,
    ) -> dict[str, object]:
        run_id = create_run_id()
        root_dir = (
            Path(output_root)
            if output_root is not None
            else default_output_root(
                base_cfg,
                run_id=run_id,
                module=module,
            )
        )
        protocol_payload = protocol_to_dict(protocol)
        protocol_sha256 = stable_hash(protocol_payload)
        repo_root = Path(base_cfg.paths.repo_root)
        governance_spec = dict(protocol.research_governance)
        explicit_campaign_db = str(campaign_db).strip() if campaign_db is not None else ""
        protocol_campaign_db = str(governance_spec.get("campaign_db") or "").strip()
        if explicit_campaign_db:
            selected_campaign_db: str | Path | None = campaign_db
            campaign_db_source = "argument"
        elif protocol_campaign_db:
            selected_campaign_db = protocol_campaign_db
            campaign_db_source = "protocol"
        else:
            selected_campaign_db = DEFAULT_CAMPAIGN_DB
            campaign_db_source = "default"
        db_path = resolve_campaign_db_path(selected_campaign_db, repo_root=repo_root)
        root_dir.mkdir(parents=True, exist_ok=True)

        source_identity = strategy_source_identity(module, repo_root=repo_root)
        role = str(governance_spec.get("role") or "diagnostic").strip().casefold()
        validation_profile = "promotion" if bool(promotion) or role == "sealed_holdout" else "draft"
        preflight = build_validation_preflight(
            module,
            repo_root=repo_root,
            profile=validation_profile,
        )
        effective_campaign_id = str(campaign_id or governance_spec.get("campaign_id") or "").strip()
        effective_candidate_id = str(candidate_id or governance_spec.get("candidate_id") or "").strip()
        governance_issues: list[dict[str, str]] = []
        if role not in {"diagnostic", "sealed_holdout"}:
            governance_issues.append(
                {
                    "severity": "error",
                    "code": "research_governance_role_invalid",
                    "message": f"unsupported research_governance.role: {role}",
                }
            )
        if bool(effective_campaign_id) != bool(effective_candidate_id):
            governance_issues.append(
                {
                    "severity": "error",
                    "code": "research_campaign_identity_incomplete",
                    "message": "campaign_id and candidate_id must be provided together",
                }
            )
        if role == "sealed_holdout" and not (effective_campaign_id and effective_candidate_id):
            governance_issues.append(
                {
                    "severity": "error",
                    "code": "sealed_holdout_identity_required",
                    "message": "sealed holdout requires a frozen campaign_id and candidate_id",
                }
            )
        if governance_issues:
            preflight["issues"] = [*(preflight.get("issues") or []), *governance_issues]
            preflight["ok"] = False
            preflight["status"] = "fail"

        verify_data_content = validation_profile == "promotion" and bool(preflight.get("ok"))
        try:
            data_identity = panel_data_identity(
                base_cfg,
                verify_content=verify_data_content,
            )
        except DataIdentityError as exc:
            preflight["issues"] = [
                *(preflight.get("issues") or []),
                {
                    "severity": "error",
                    "code": "panel_data_identity_invalid",
                    "message": str(exc),
                },
            ]
            preflight["ok"] = False
            preflight["status"] = "fail"
            panel_cfg = getattr(base_cfg, "panel", None)
            data_identity = {
                "source": str(getattr(panel_cfg, "source", "") or "").strip().casefold(),
                "path": str(getattr(panel_cfg, "path", "") or "").strip(),
                "exists": False,
                "content_verified": False,
                "error": str(exc),
            }
        campaign_snapshot: dict[str, object] = {}
        holdout_claimed = False
        if preflight.get("ok") and effective_campaign_id and effective_candidate_id:
            if role == "sealed_holdout":
                campaign_snapshot = claim_holdout(
                    db_path,
                    campaign_id=effective_campaign_id,
                    candidate_id=effective_candidate_id,
                    source_sha256=str(source_identity.get("sha256") or ""),
                    protocol_sha256=protocol_sha256,
                    validation_run_id=run_id,
                )
                holdout_claimed = True
            else:
                campaign_snapshot = register_candidate(
                    db_path,
                    campaign_id=effective_campaign_id,
                    candidate_id=effective_candidate_id,
                    source_sha256=str(source_identity.get("sha256") or ""),
                    module=module,
                )

        case_results = {}
        if preflight.get("ok"):
            case_results = {
                case.name: run_validation_case(
                    session=self.session,
                    base_cfg=base_cfg,
                    module=module,
                    protocol=protocol,
                    case=case,
                    root_dir=root_dir,
                    strict_contract=validation_profile == "promotion",
                )
                for case in protocol.cases
            }
        else:
            case_results = {
                case.name: {
                    "name": case.name,
                    "description": case.description,
                    "gate_required": bool(case.gate_required),
                    "status": "skipped",
                    "error": "validation_preflight_failed",
                    "window": {"start_time": case.start_time, "end_time": case.end_time},
                    "overrides": {},
                    "stock_code_list": list(case.stock_code_list or ()),
                    "result": {},
                    "contract_check": {
                        "ok": None,
                        "status": "not_evaluated",
                        "strict": validation_profile == "promotion",
                        "evaluated": False,
                        "issues": [],
                    },
                    "diagnostics": {},
                    "diagnostics_snapshot": {},
                    "artifacts": {"case_root": str(root_dir / "cases" / case.name)},
                    "meta": {},
                }
                for case in protocol.cases
            }
        git_identity = next(
            (
                dict((payload.get("meta") or {}).get("git") or {})
                for payload in case_results.values()
                if isinstance((payload.get("meta") or {}).get("git"), dict) and (payload.get("meta") or {}).get("git")
            ),
            {},
        )
        if not git_identity:
            try:
                injected_git = json.loads(os.environ.get("CODEX_GIT_IDENTITY_JSON", ""))
            except json.JSONDecodeError:
                injected_git = {}
            if isinstance(injected_git, dict):
                git_identity = dict(injected_git)
        identity = {
            "strategy_source": source_identity,
            "protocol_sha256": protocol_sha256,
            "data": data_identity,
            "case_config_hashes": {
                name: stable_hash((payload.get("meta") or {}).get("config") or {})
                for name, payload in sorted(case_results.items(), key=lambda item: item[0].casefold())
            },
            "git": git_identity,
        }
        gate_results = evaluate_gates(case_results, protocol.gates)
        multiplicity_gate: dict[str, object] | None = None
        if role == "sealed_holdout" and campaign_snapshot:
            p_values = [
                payload.get("result", {}).get("positive_mean_p_value")
                for payload in case_results.values()
                if bool(payload.get("gate_required", True)) and isinstance(payload.get("result"), dict)
            ]
            numeric_p_values = [float(value) for value in p_values if value is not None]
            familywise_alpha = campaign_snapshot.get("familywise_alpha")
            max_candidates = campaign_snapshot.get("max_candidates")
            if (
                not isinstance(familywise_alpha, (int, float))
                or isinstance(familywise_alpha, bool)
                or not isinstance(max_candidates, int)
                or isinstance(max_candidates, bool)
                or max_candidates < 1
            ):
                raise ValueError("campaign multiplicity settings are invalid")
            corrected_alpha = float(familywise_alpha) / max_candidates
            worst_p_value = max(numeric_p_values) if numeric_p_values else None
            multiplicity_gate = {
                "name": "sealed_holdout_bonferroni",
                "kind": "threshold",
                "level": "hard",
                "description": "One-sided positive-mean test adjusted by the pre-registered candidate budget.",
                "case": "sealed_holdout",
                "path": "result.positive_mean_p_value",
                "display_path": "sealed_holdout.result.positive_mean_p_value",
                "status": "PASS" if worst_p_value is not None and worst_p_value <= corrected_alpha else "FAIL",
                "passed": bool(worst_p_value is not None and worst_p_value <= corrected_alpha),
                "actual": worst_p_value,
                "expected": corrected_alpha,
                "op": "<=",
                "base_case": "",
                "base_value": None,
                "relative_drop": None,
                "error": "" if worst_p_value is not None else "insufficient_daily_returns",
            }
            gate_results.append(multiplicity_gate)
        verdict = build_verdict(
            case_results,
            gate_results,
            protocol,
            preflight=preflight,
            require_strict_contract=validation_profile == "promotion",
        )
        research_governance = {
            "role": role,
            "sealed_holdout": role == "sealed_holdout",
            "campaign_id": effective_campaign_id,
            "candidate_id": effective_candidate_id,
            "campaign_db": str(db_path),
            "campaign_db_source": campaign_db_source,
            "campaign_status": campaign_snapshot.get("status") if campaign_snapshot else "",
            "sealed_protocol_sha256": (campaign_snapshot.get("sealed_protocol_sha256") if campaign_snapshot else ""),
            "protocol_sha256": protocol_sha256,
            "max_candidates": campaign_snapshot.get("max_candidates") if campaign_snapshot else None,
            "familywise_alpha": campaign_snapshot.get("familywise_alpha") if campaign_snapshot else None,
            "multiplicity_gate": multiplicity_gate or {},
            "holdout_claimed": holdout_claimed,
            "validation_profile": validation_profile,
        }
        if campaign_snapshot and effective_campaign_id and effective_candidate_id:
            record_attempt(
                db_path,
                campaign_id=effective_campaign_id,
                candidate_id=effective_candidate_id,
                source_sha256=str(source_identity.get("sha256") or ""),
                phase=role,
                status="pass" if verdict.get("passed") else "reject",
                run_ref=run_id,
                payload={"decision": verdict.get("decision"), "protocol_sha256": protocol_sha256},
            )
        if holdout_claimed:
            complete_holdout(
                db_path,
                campaign_id=effective_campaign_id,
                candidate_id=effective_candidate_id,
                decision=str(verdict.get("decision") or ""),
            )

        report: dict[str, object] = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "validation_run_id": run_id,
            "module": module,
            "protocol_version": protocol.version,
            "protocol_description": protocol.description,
            "promotion_target": protocol.promotion_target,
            "output_root": str(root_dir),
            "preflight": preflight,
            "identity": identity,
            "research_governance": research_governance,
            "summary": verdict,
            "cases": [case_results[case.name] for case in protocol.cases],
            "gates": gate_results,
        }
        write_validation_report(
            root_dir=root_dir,
            protocol=protocol,
            module=module,
            report=report,
        )
        return report


__all__ = ["StrategyValidationService"]
