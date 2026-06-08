import argparse
import json
import time
from datetime import datetime
from uuid import uuid4

from pncp_query.config import (
    CNPJS_BRUTOS_JSON,
    CNPJS_FINAIS_CSV,
    ENABLE_DB_CHECKPOINT,
    LICITACOES_CSV,
    PDF_DIR,
    PDF_RETENTION_DAYS,
    RUN_LIMIT_DEFAULT,
    janela_padrao,
)
from pncp_query.controllers.downloader_controller import DownloaderController
from pncp_query.controllers.limpeza_controller import LimpezaController
from pncp_query.controllers.parser_pdf_controller import ParserPDFController
from pncp_query.controllers.rastreador_controller import RastreadorController
from pncp_query.services.database_service import DatabaseService
from pncp_query.services.failure_notifier import FailureNotifier
from pncp_query.services.repositories import (
    DocumentRepository,
    LeadRepository,
    MetricsRepository,
    RunRepository,
    SearchRepository,
)


def parse_args():
    data_inicial, data_final = janela_padrao()
    parser = argparse.ArgumentParser(description="Pipeline completo PNCP: busca, download, parser e limpeza.")
    parser.add_argument("--data-inicial", default=data_inicial, help="Data inicial YYYY-MM-DD.")
    parser.add_argument("--data-final", default=data_final, help="Data final YYYY-MM-DD.")
    parser.add_argument(
        "--limite",
        type=int,
        default=None,
        help="Limite por termo/perfil de busca. Sem valor usa limite estatistico.",
    )
    parser.add_argument("--pausa-run", type=float, default=0.5, help="Pausa entre paginas do rastreador.")
    parser.add_argument("--pausa-downloader", type=float, default=1.5, help="Pausa entre compras no downloader.")
    parser.add_argument("--resume-run-id", help="Retoma uma execucao ja registrada no PostgreSQL.")
    parser.add_argument(
        "--force-parse",
        action="store_true",
        help="Reprocessa PDFs mesmo que ja existam no checkpoint.",
    )
    parser.add_argument(
        "--cleanup-retention",
        action="store_true",
        help="Remove PDFs locais antigos registrados no banco.",
    )
    parser.add_argument("--migrate-only", action="store_true", help="Executa migracoes do PostgreSQL e encerra.")
    parser.add_argument(
        "--no-db-checkpoint",
        action="store_true",
        help="Executa no modo legado sem checkpoints no PostgreSQL.",
    )
    return parser.parse_args()


def _novo_run_id():
    return f"pncp-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"


def _deve_pular(run_repo, run_id, stage):
    return run_repo.stage_status(run_id, stage) == "COMPLETED"


def _emit_event(run_id, stage, level, message, **payload):
    event = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "stage": stage,
        "level": level,
        "message": message,
        **payload,
    }
    print(json.dumps(event, ensure_ascii=False))


def _executar_etapa(run_repo, metrics_repo, run_id, stage, func, skip=False, notifier=None, notify_context=None):
    if skip:
        print(f"[SKIP] Etapa ja concluida: {stage}")
        _emit_event(run_id, stage, "INFO", "Etapa pulada por checkpoint concluido.")
        metrics_repo.log(run_id, stage, "Etapa pulada por checkpoint concluido.")
        return None
    print(f"[RUN] {stage}")
    _emit_event(run_id, stage, "INFO", "Etapa iniciada.")
    run_repo.start_stage(run_id, stage)
    metrics_repo.log(run_id, stage, "Etapa iniciada.")
    inicio = time.perf_counter()
    try:
        resultado = func()
    except Exception as exc:
        duracao_ms = int((time.perf_counter() - inicio) * 1000)
        metrics_repo.set_metric(run_id, f"{stage}_duration_ms", duracao_ms)
        run_repo.fail_stage(run_id, stage, exc)
        metrics_repo.log(run_id, stage, str(exc), level="ERROR")
        metrics_repo.log(run_id, stage, f"Falha critica na etapa {stage}.", level="CRITICAL")
        _emit_event(run_id, stage, "CRITICAL", str(exc), duration_ms=duracao_ms)
        if notifier:
            notifier.notify(
                "pipeline_stage_failed",
                run_id=run_id,
                stage=stage,
                message=str(exc),
                duration_ms=duracao_ms,
                **(notify_context or {}),
            )
        raise
    duracao_ms = int((time.perf_counter() - inicio) * 1000)
    metrics_repo.set_metric(run_id, f"{stage}_duration_ms", duracao_ms)
    run_repo.complete_stage(run_id, stage)
    metrics_repo.log(run_id, stage, "Etapa concluida.")
    _emit_event(run_id, stage, "INFO", "Etapa concluida.", duration_ms=duracao_ms)
    return resultado


if __name__ == "__main__":
    args = parse_args()
    usar_db = ENABLE_DB_CHECKPOINT and not args.no_db_checkpoint

    if usar_db:
        db = DatabaseService()
        db.migrate()
        if args.migrate_only:
            print("Migracoes aplicadas com sucesso.")
            raise SystemExit(0)

        run_repo = RunRepository(db)
        search_repo = SearchRepository(db)
        document_repo = DocumentRepository(db)
        lead_repo = LeadRepository(db)
        metrics_repo = MetricsRepository(db)
        notifier = FailureNotifier()

        if args.cleanup_retention:
            removidos = document_repo.cleanup_old_pdfs(PDF_RETENTION_DAYS)
            print(f"Retencao concluida. PDFs removidos: {len(removidos)}")
            raise SystemExit(0)

        if args.resume_run_id:
            run_id = args.resume_run_id
            run = run_repo.get_run(run_id)
            if not run:
                raise SystemExit(f"run_id nao encontrado: {run_id}")
            limite = int(run["limite_usado"] or RUN_LIMIT_DEFAULT)
            limite_origem = "resume"
            print(f"Retomando execucao: {run_id}")
        else:
            run_id = _novo_run_id()
            if args.limite is None:
                limite, limite_origem = run_repo.suggest_limit()
            else:
                limite, limite_origem = args.limite, "manual"
            run_repo.create_run(
                run_id,
                args.data_inicial,
                args.data_final,
                limite,
                limite_origem,
                vars(args),
            )
            print(f"Execucao criada: {run_id} | limite={limite} ({limite_origem})")

        notify_context = {
            "data_inicial": args.data_inicial,
            "data_final": args.data_final,
            "limite": limite,
            "limite_origem": limite_origem,
        }

        registros = _executar_etapa(
            run_repo,
            metrics_repo,
            run_id,
            "search",
            lambda: RastreadorController(search_repository=search_repo).executar(
                args.data_inicial,
                args.data_final,
                limite,
                args.pausa_run,
                run_id=run_id,
            ),
            skip=_deve_pular(run_repo, run_id, "search"),
            notifier=notifier,
            notify_context=notify_context,
        )
        if registros is not None:
            total_registros = registros if isinstance(registros, int) else len(registros)
            run_repo.update_counts(run_id, licitacoes_persistidas=total_registros)
        else:
            total = search_repo.count_for_run(run_id)
            run_repo.update_counts(run_id, licitacoes_persistidas=total)

        baixados = _executar_etapa(
            run_repo,
            metrics_repo,
            run_id,
            "download",
            lambda: DownloaderController(document_repository=document_repo, search_repository=search_repo).executar(
                pdf_dir=PDF_DIR,
                pausa=args.pausa_downloader,
                run_id=run_id,
            ),
            skip=_deve_pular(run_repo, run_id, "download"),
            notifier=notifier,
            notify_context=notify_context,
        )
        if baixados is not None:
            run_repo.update_counts(
                run_id,
                pdfs_baixados=baixados,
                compras_qualificadas=document_repo.count_qualified_purchases(run_id),
            )

        resultados = _executar_etapa(
            run_repo,
            metrics_repo,
            run_id,
            "parse",
            lambda: ParserPDFController(document_repository=document_repo).executar(
                run_id=run_id,
                force_parse=args.force_parse,
            ),
            skip=_deve_pular(run_repo, run_id, "parse") and not args.force_parse,
            notifier=notifier,
            notify_context=notify_context,
        )
        if resultados is not None:
            cnpjs_derrotados_brutos = (
                resultados.get("cnpjs_derrotados_brutos", 0)
                if isinstance(resultados, dict)
                else sum(len(resultado.get("cnpjs_derrotados", [])) for resultado in resultados)
            )
            run_repo.update_counts(
                run_id,
                pdfs_processados=document_repo.count_parsed_pdfs(run_id),
                cnpjs_derrotados_brutos=cnpjs_derrotados_brutos,
            )

        cnpjs = _executar_etapa(
            run_repo,
            metrics_repo,
            run_id,
            "cleanup",
            lambda: LimpezaController(lead_repository=lead_repo).executar(
                run_id=run_id,
            ),
            skip=_deve_pular(run_repo, run_id, "cleanup"),
            notifier=notifier,
            notify_context=notify_context,
        )
        if cnpjs is None:
            total_cnpjs = lead_repo.count_for_run(run_id)
        elif isinstance(cnpjs, dict):
            total_cnpjs = cnpjs.get("cnpjs_finais_unicos", 0)
        else:
            total_cnpjs = len(cnpjs)
        run_repo.update_counts(run_id, cnpjs_finais_unicos=total_cnpjs)
        if total_cnpjs == 0:
            metrics_repo.log(run_id, "cleanup", "Rodada concluida sem leads.", level="CRITICAL")
            _emit_event(run_id, "cleanup", "CRITICAL", "Rodada concluida sem leads.")
            notifier.notify(
                "pipeline_zero_leads",
                run_id=run_id,
                stage="cleanup",
                message="Rodada concluida sem leads.",
                total_cnpjs=total_cnpjs,
                **notify_context,
            )

        _executar_etapa(
            run_repo,
            metrics_repo,
            run_id,
            "export",
            lambda: lead_repo.export_csv(run_id, CNPJS_FINAIS_CSV),
            skip=_deve_pular(run_repo, run_id, "export"),
            notifier=notifier,
            notify_context=notify_context,
        )

        run_repo.complete_run(run_id)
        print(f"Pipeline concluido: {run_id}")
        print(f"Registros finais no banco: {total_cnpjs}")
        print(f"CSV final exportado: {CNPJS_FINAIS_CSV}")
        raise SystemExit(0)

    limite = args.limite if args.limite is not None else RUN_LIMIT_DEFAULT
    if args.migrate_only or args.cleanup_retention:
        print("Use checkpoints com PostgreSQL para migracao/retencao, ou remova --no-db-checkpoint.")
        raise SystemExit(1)
    RastreadorController().executar(args.data_inicial, args.data_final, limite, args.pausa_run)
    DownloaderController().executar(LICITACOES_CSV, PDF_DIR, args.pausa_downloader)
    ParserPDFController().executar(PDF_DIR, CNPJS_BRUTOS_JSON)
    LimpezaController().executar(CNPJS_BRUTOS_JSON, CNPJS_FINAIS_CSV)
    print(f"CSV final exportado: {CNPJS_FINAIS_CSV}")
