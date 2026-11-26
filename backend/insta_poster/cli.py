from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from insta_poster.config import AppPaths
from insta_poster.models import AdapterConfigUpdate, PoolImageUpdate, PublicationCreate, PublicationUpdate
from insta_poster.service import InstaPosterService

app = typer.Typer(
    name="insta-poster",
    help="CLI + Local Webapp + REST API para gestionar tu pool de imágenes y publicar posts e historias en Instagram.",
    no_args_is_help=True,
)
pool_app = typer.Typer(help="Gestionar el pool local de imágenes.")
post_app = typer.Typer(help="Crear y publicar posts o carruseles de feed.")
story_app = typer.Typer(help="Crear y publicar historias (Stories 9:16).")
queue_app = typer.Typer(help="Consultar, publicar o cancelar la cola e historial.")
adapter_app = typer.Typer(help="Gestionar adaptadores de Instagram (dry_run, graph_api).")
insights_app = typer.Typer(help="Sincronizar y consultar métricas (Insights) de cuenta, posts e historias.")
user_app = typer.Typer(help="Gestionar usuarios de autenticación de la webapp y API.")
apikey_app = typer.Typer(help="Gestionar API Keys para agentes y automatizaciones.")

app.add_typer(pool_app, name="pool")
app.add_typer(post_app, name="post")
app.add_typer(story_app, name="story")
app.add_typer(queue_app, name="queue")
app.add_typer(adapter_app, name="adapter")
app.add_typer(insights_app, name="insights")
app.add_typer(user_app, name="user")
app.add_typer(apikey_app, name="apikey")

console = Console()


def _get_service(data_dir: Optional[Path] = None) -> InstaPosterService:
    return InstaPosterService(AppPaths.from_data_dir(data_dir))


def _emit(data: Any, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return
    console.print(data)


def _split_csv(value: Optional[str]) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


# ----------------------------------------------------------------------
# Server & status
# ----------------------------------------------------------------------
@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", envvar="INSTA_POSTER_HOST", help="Host de escucha (usar 0.0.0.0 en Docker/Dokploy)."),
    port: int = typer.Option(8000, envvar="INSTA_POSTER_PORT", help="Puerto HTTP de la webapp y REST API."),
    data_dir: Optional[Path] = typer.Option(None, help="Directorio de datos (SQLite + imágenes)."),
    reload: bool = typer.Option(False, help="Habilitar recarga automática para desarrollo."),
) -> None:
    """Inicia la webapp local, la REST API (/docs) y el scheduler de publicaciones."""
    if data_dir is not None:
        os.environ["INSTA_POSTER_DATA_DIR"] = str(data_dir.expanduser().resolve())
    console.print(f"[bold green]Insta-Poster[/bold green] corriendo en [link=http://{host}:{port}]http://{host}:{port}[/link]")
    console.print(f"OpenAPI / Swagger UI: [link=http://{host}:{port}/docs]http://{host}:{port}/docs[/link]")
    uvicorn.run(
        "insta_poster.api:app_factory",
        host=host,
        port=port,
        factory=True,
        reload=reload,
    )


@app.command()
def status(
    as_json: bool = typer.Option(False, "--json", help="Salida estructurada en JSON (ideal para agentes)."),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir", help="Directorio de datos."),
) -> None:
    """Muestra estadísticas del pool, cola y adaptador activo."""
    svc = _get_service(data_dir)
    stats = svc.get_stats().model_dump()
    adapters = [a.model_dump() for a in svc.list_adapters(verify_remote=False)]
    payload = {"stats": stats, "adapters": adapters, "data_dir": str(svc.paths.data_dir)}
    if as_json:
        _emit(payload, True)
        return

    table = Table(title="Insta-Poster Status")
    table.add_column("Métrica", style="cyan")
    table.add_column("Valor", style="bold")
    table.add_row("Pool total", str(stats["pool_total"]))
    table.add_row("Disponibles", str(stats["pool_available"]))
    table.add_row("En cola / programadas", str(stats["publications_scheduled"]))
    table.add_row("Posts publicados", str(stats["posts_published"]))
    table.add_row("Stories publicadas", str(stats["stories_published"]))
    table.add_row("Fallidas", str(stats["publications_failed"]))
    table.add_row("Adaptador por defecto", stats["default_adapter"])
    table.add_row("Scheduler activo", "Sí" if stats["scheduler_enabled"] else "No")
    console.print(table)


# ----------------------------------------------------------------------
# Pool commands
# ----------------------------------------------------------------------
@pool_app.command("list")
def pool_list(
    status_filter: Optional[str] = typer.Option(None, "--status", help="available|queued|published|archived"),
    tag: Optional[str] = typer.Option(None, "--tag", help="Filtrar por etiqueta."),
    suitable_for: Optional[str] = typer.Option(None, "--format", help="post|story"),
    unused_for: Optional[str] = typer.Option(None, "--unused-for", help="post|story|any"),
    unused: bool = typer.Option(False, "--unused", help="Atajo para --unused-for any."),
    search: Optional[str] = typer.Option(None, "--search", help="Buscar en nombre, notas o tags."),
    limit: int = typer.Option(100, "--limit"),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Lista las imágenes del pool."""
    svc = _get_service(data_dir)
    images = svc.list_pool(
        status=status_filter,
        tag=tag,
        suitable_for=suitable_for,
        unused_for=unused_for or ("any" if unused else None),
        search=search,
        limit=limit,
    )
    if as_json:
        _emit([img.model_dump() for img in images], True)
        return

    table = Table(title=f"Pool de Imágenes ({len(images)})")
    table.add_column("ID", style="cyan")
    table.add_column("Archivo")
    table.add_column("Resolución")
    table.add_column("Estado")
    table.add_column("Usos (P/S)")
    table.add_column("Tags")
    for img in images:
        table.add_row(
            img.id,
            img.original_filename[:32],
            f"{img.width}x{img.height}",
            img.status,
            f"{img.times_used_post}/{img.times_used_story}",
            ", ".join(img.tags),
        )
    console.print(table)


@pool_app.command("add")
def pool_add(
    image_path: Path = typer.Argument(..., help="Ruta a la imagen local."),
    tags: Optional[str] = typer.Option(None, "--tags", help="Etiquetas separadas por coma."),
    notes: str = typer.Option("", "--notes", help="Descripción o contexto para el agente."),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Agrega una imagen al pool."""
    svc = _get_service(data_dir)
    try:
        img, created = svc.add_image_file(image_path, tags=_split_csv(tags), notes=notes, actor="cli")
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    payload = {"created": created, "image": img.model_dump()}
    if as_json:
        _emit(payload, True)
    else:
        verb = "Agregada" if created else "Ya existía (deduplicada)"
        console.print(f"[green]{verb}[/green]: {img.id} ({img.original_filename})")


@pool_app.command("sync")
def pool_sync(
    folder: Path = typer.Argument(..., help="Carpeta local con imágenes."),
    tags: Optional[str] = typer.Option(None, "--tags", help="Etiquetas a aplicar a las imágenes nuevas."),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Importa todas las imágenes de una carpeta local (sin duplicados)."""
    svc = _get_service(data_dir)
    try:
        res = svc.sync_folder(str(folder), tags=_split_csv(tags), recursive=recursive, actor="cli")
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if as_json:
        _emit(res.model_dump(), True)
    else:
        console.print(
            f"[green]Sync completo[/green]: {len(res.added)} agregadas, "
            f"{res.skipped_duplicates} duplicadas omitidas, {len(res.errors)} errores."
        )


@pool_app.command("inspect")
def pool_inspect(
    image_id: str = typer.Argument(..., help="ID de la imagen."),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Muestra metadata completa y ruta absoluta de una imagen del pool."""
    svc = _get_service(data_dir)
    try:
        img = svc.get_image(image_id)
    except KeyError as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    _emit(img.model_dump(), as_json)


@pool_app.command("update")
def pool_update(
    image_id: str = typer.Argument(..., help="ID de la imagen."),
    tags: Optional[str] = typer.Option(None, "--tags", help="Reemplazar etiquetas (separadas por coma)."),
    notes: Optional[str] = typer.Option(None, "--notes", help="Actualizar notas/descripción."),
    status_val: Optional[str] = typer.Option(None, "--status", help="available|queued|published|archived"),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Actualiza tags, notas o estado de una imagen del pool."""
    svc = _get_service(data_dir)
    try:
        img = svc.update_image(
            image_id,
            PoolImageUpdate(
                tags=_split_csv(tags) if tags is not None else None,
                notes=notes,
                status=status_val,  # type: ignore[arg-type]
            ),
            actor="cli",
        )
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    _emit(img.model_dump(), as_json)


@pool_app.command("pick")
def pool_pick(
    pub_type: str = typer.Option("post", "--type", help="post|story"),
    count: int = typer.Option(1, "--count", help="Cantidad de imágenes a elegir."),
    tag: Optional[str] = typer.Option(None, "--tag"),
    unused: bool = typer.Option(True, "--unused/--allow-used"),
    randomize: bool = typer.Option(True, "--random/--ordered"),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Selecciona imágenes candidatas del pool (ideal para agentes)."""
    svc = _get_service(data_dir)
    picked = svc.pick_images(
        pub_type=pub_type,
        count=count,
        tag=tag,
        unused_only=unused,
        randomize=randomize,
    )
    if as_json:
        _emit([p.model_dump() for p in picked], True)
        return
    for p in picked:
        console.print(f"[cyan]{p.id}[/cyan] {p.original_filename} ({p.width}x{p.height}) -> {p.filepath}")


# ----------------------------------------------------------------------
# Post & Story commands
# ----------------------------------------------------------------------
@post_app.command("create")
def post_create(
    image: list[str] = typer.Option(..., "--image", "-i", help="ID de imagen del pool (repetir para carrusel)."),
    caption: str = typer.Option("", "--caption", "-c", help="Texto del post."),
    hashtags: Optional[str] = typer.Option(None, "--hashtags", help="Hashtags separados por coma o espacio."),
    fit: str = typer.Option("auto", "--fit", help="auto|crop|pad_blur|pad_solid"),
    schedule: Optional[str] = typer.Option(None, "--schedule", help="Fecha ISO 8601 (ej. 2026-11-25T18:00:00Z)."),
    now: bool = typer.Option(False, "--now", help="Publicar inmediatamente."),
    adapter: Optional[str] = typer.Option(None, "--adapter", help="dry_run|graph_api"),
    actor: str = typer.Option("cli-agent", "--actor", help="Identificador del agente o usuario."),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Crea (y opcionalmente publica o programa) un post o carrusel de feed."""
    svc = _get_service(data_dir)
    pub_type = "carousel" if len(image) > 1 else "post"
    try:
        pub = svc.create_publication(
            PublicationCreate(
                pub_type=pub_type,  # type: ignore[arg-type]
                image_ids=image,
                caption=caption,
                hashtags=[hashtags] if hashtags else [],
                fit_mode=fit,  # type: ignore[arg-type]
                adapter=adapter,  # type: ignore[arg-type]
                scheduled_at=schedule,
                publish_now=now,
                created_by=actor,
            )
        )
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    if as_json:
        _emit(pub.model_dump(), True)
    else:
        console.print(f"[green]{pub.pub_type.upper()} {pub.id}[/green] status=[bold]{pub.status}[/bold] adapter={pub.adapter}")
        if pub.external_id:
            console.print(f"External ID: {pub.external_id} ({pub.external_url or '-'})")
        if pub.error_message:
            console.print(f"[red]Error:[/red] {pub.error_message}")


@story_app.command("create")
def story_create(
    image: str = typer.Option(..., "--image", "-i", help="ID de imagen del pool."),
    fit: str = typer.Option("auto", "--fit", help="auto|pad_blur|crop|pad_solid"),
    schedule: Optional[str] = typer.Option(None, "--schedule", help="Fecha ISO 8601."),
    now: bool = typer.Option(False, "--now", help="Publicar inmediatamente."),
    adapter: Optional[str] = typer.Option(None, "--adapter", help="dry_run|graph_api"),
    actor: str = typer.Option("cli-agent", "--actor"),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Crea (y opcionalmente publica o programa) una historia (Story 9:16)."""
    svc = _get_service(data_dir)
    try:
        pub = svc.create_publication(
            PublicationCreate(
                pub_type="story",
                image_ids=[image],
                fit_mode=fit,  # type: ignore[arg-type]
                adapter=adapter,  # type: ignore[arg-type]
                scheduled_at=schedule,
                publish_now=now,
                created_by=actor,
            )
        )
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    if as_json:
        _emit(pub.model_dump(), True)
    else:
        console.print(f"[green]STORY {pub.id}[/green] status=[bold]{pub.status}[/bold] adapter={pub.adapter}")
        if pub.external_id:
            console.print(f"External ID: {pub.external_id}")
        if pub.error_message:
            console.print(f"[red]Error:[/red] {pub.error_message}")


# ----------------------------------------------------------------------
# Queue commands
# ----------------------------------------------------------------------
@queue_app.command("list")
def queue_list(
    status_filter: Optional[str] = typer.Option(None, "--status", help="draft|scheduled|published|failed"),
    pub_type: Optional[str] = typer.Option(None, "--type", help="post|carousel|story"),
    limit: int = typer.Option(50, "--limit"),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Lista las publicaciones (borradores, programadas, publicadas, fallidas)."""
    svc = _get_service(data_dir)
    pubs = svc.list_publications(status=status_filter, pub_type=pub_type, limit=limit)
    if as_json:
        _emit([p.model_dump() for p in pubs], True)
        return
    table = Table(title=f"Cola e Historial ({len(pubs)})")
    table.add_column("ID", style="cyan")
    table.add_column("Tipo")
    table.add_column("Estado")
    table.add_column("Adaptador")
    table.add_column("Programado / Publicado")
    table.add_column("Caption")
    for p in pubs:
        table.add_row(
            p.id,
            p.pub_type,
            p.status,
            p.adapter,
            p.published_at or p.scheduled_at or "-",
            (p.caption[:36] + "…") if len(p.caption) > 36 else p.caption,
        )
    console.print(table)


@queue_app.command("edit")
def queue_edit(
    pub_id: str = typer.Argument(..., help="ID del borrador o publicación programada."),
    pub_type: Optional[str] = typer.Option(None, "--type", help="post|carousel|story"),
    image: Optional[list[str]] = typer.Option(None, "--image", "-i", help="Reemplazar imágenes (repetir para carrusel)."),
    caption: Optional[str] = typer.Option(None, "--caption", "-c", help="Nuevo texto del post."),
    hashtags: Optional[str] = typer.Option(None, "--hashtags", help="Nuevos hashtags."),
    fit: Optional[str] = typer.Option(None, "--fit", help="auto|crop|pad_blur|pad_solid"),
    schedule: Optional[str] = typer.Option(None, "--schedule", help="Nueva fecha ISO 8601."),
    clear_schedule: bool = typer.Option(False, "--clear-schedule", help="Quitar programación y volver a borrador."),
    adapter: Optional[str] = typer.Option(None, "--adapter", help="dry_run|graph_api"),
    now: bool = typer.Option(False, "--now", help="Guardar y publicar inmediatamente."),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Edita un borrador o publicación programada (no publicada)."""
    svc = _get_service(data_dir)
    try:
        pub = svc.update_publication(
            pub_id,
            PublicationUpdate(
                pub_type=pub_type,  # type: ignore[arg-type]
                image_ids=image if image else None,
                caption=caption,
                hashtags=[hashtags] if hashtags is not None else None,
                fit_mode=fit,  # type: ignore[arg-type]
                adapter=adapter,  # type: ignore[arg-type]
                scheduled_at=schedule,
                clear_schedule=clear_schedule,
                publish_now=now,
            ),
            actor="cli",
        )
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    _emit(pub.model_dump(), as_json)


@queue_app.command("publish")
def queue_publish(
    pub_id: str = typer.Argument(..., help="ID de la publicación."),
    adapter: Optional[str] = typer.Option(None, "--adapter"),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Publica inmediatamente un borrador o elemento programado."""
    svc = _get_service(data_dir)
    try:
        pub = svc.publish_publication(pub_id, adapter_override=adapter, actor="cli")
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    _emit(pub.model_dump(), as_json)


@queue_app.command("cancel")
def queue_cancel(
    pub_id: str = typer.Argument(..., help="ID de la publicación a eliminar."),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Elimina una publicación de la cola."""
    svc = _get_service(data_dir)
    try:
        svc.delete_publication(pub_id, actor="cli")
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    _emit({"deleted": True, "id": pub_id}, as_json)


@queue_app.command("run-due")
def queue_run_due(
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Ejecuta todas las publicaciones programadas cuya fecha ya venció."""
    svc = _get_service(data_dir)
    results = svc.run_due_scheduled()
    _emit([p.model_dump() for p in results], as_json)


# ----------------------------------------------------------------------
# Adapter commands
# ----------------------------------------------------------------------
@adapter_app.command("list")
def adapter_list(
    verify_remote: bool = typer.Option(False, "--verify", help="Verificar conexión real contra APIs remotas."),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Lista los adaptadores disponibles y su estado."""
    svc = _get_service(data_dir)
    adapters = svc.list_adapters(verify_remote=verify_remote)
    if as_json:
        _emit([a.model_dump() for a in adapters], True)
        return
    table = Table(title="Adaptadores de Instagram")
    table.add_column("Nombre", style="cyan")
    table.add_column("Default")
    table.add_column("Configurado")
    table.add_column("Disponible")
    table.add_column("Detalle")
    for a in adapters:
        table.add_row(
            a.name,
            "★" if a.is_default else "",
            "Sí" if a.configured else "No",
            "Sí" if a.available else "No",
            a.message,
        )
    console.print(table)


@adapter_app.command("set-default")
def adapter_set_default(
    name: str = typer.Argument(..., help="dry_run | graph_api"),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Define el adaptador por defecto para nuevas publicaciones."""
    svc = _get_service(data_dir)
    if name not in ("dry_run", "graph_api"):
        typer.echo("Adaptador inválido. Usá: dry_run o graph_api", err=True)
        raise typer.Exit(code=1)
    updated = svc.update_settings(AdapterConfigUpdate(default_adapter=name), actor="cli")  # type: ignore[arg-type]
    _emit(updated, as_json)


# ----------------------------------------------------------------------
# User management commands
# ----------------------------------------------------------------------
@user_app.command("list")
def user_list(
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Lista los usuarios registrados en el sistema."""
    svc = _get_service(data_dir)
    users = svc.list_users()
    if as_json:
        _emit([u.model_dump() for u in users], True)
        return
    table = Table(title=f"Usuarios ({len(users)})")
    table.add_column("ID", style="cyan")
    table.add_column("Usuario", style="bold")
    table.add_column("Creado")
    table.add_column("Último ingreso")
    for u in users:
        table.add_row(u.id, u.username, u.created_at, u.last_login_at or "-")
    console.print(table)


@user_app.command("create")
def user_create(
    username: str = typer.Argument(..., help="Nombre de usuario."),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Contraseña (si se omite, se pide de forma oculta)."),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Crea un nuevo usuario para acceder a la webapp y API."""
    svc = _get_service(data_dir)
    pw = password if password is not None else typer.prompt("Contraseña", hide_input=True, confirmation_prompt=True)
    try:
        user = svc.create_user(username, pw, actor="cli")
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if as_json:
        _emit(user.model_dump(), True)
    else:
        console.print(f"[green]Usuario creado[/green]: {user.username} ({user.id})")


@user_app.command("passwd")
def user_passwd(
    username: str = typer.Argument(..., help="Nombre de usuario."),
    password: Optional[str] = typer.Option(None, "--password", "-p", help="Nueva contraseña."),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Cambia la contraseña de un usuario existente y revoca sus sesiones activas."""
    svc = _get_service(data_dir)
    user = svc.get_user_by_username(username)
    if not user:
        msg = f"Usuario no encontrado: {username}"
        typer.echo(json.dumps({"error": msg}) if as_json else f"Error: {msg}", err=True)
        raise typer.Exit(code=1)
    pw = password if password is not None else typer.prompt("Nueva contraseña", hide_input=True, confirmation_prompt=True)
    try:
        updated = svc.set_user_password(user.id, pw, actor="cli")
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if as_json:
        _emit(updated.model_dump(), True)
    else:
        console.print(f"[green]Contraseña actualizada[/green]: {updated.username}")


@user_app.command("delete")
def user_delete(
    username: str = typer.Argument(..., help="Nombre de usuario a eliminar."),
    force: bool = typer.Option(False, "--force", help="Permite eliminar incluso si es el último usuario."),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Elimina un usuario del sistema."""
    svc = _get_service(data_dir)
    user = svc.get_user_by_username(username)
    if not user:
        msg = f"Usuario no encontrado: {username}"
        typer.echo(json.dumps({"error": msg}) if as_json else f"Error: {msg}", err=True)
        raise typer.Exit(code=1)
    try:
        svc.delete_user(user.id, actor="cli", allow_delete_last=force)
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if as_json:
        _emit({"deleted": True, "id": user.id, "username": user.username}, True)
    else:
        console.print(f"[green]Usuario eliminado[/green]: {user.username}")


# ----------------------------------------------------------------------
# API Key management commands
# ----------------------------------------------------------------------
@apikey_app.command("list")
def apikey_list(
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Lista las API Keys registradas."""
    svc = _get_service(data_dir)
    keys = svc.list_api_keys()
    if as_json:
        _emit([k.model_dump() for k in keys], True)
        return
    table = Table(title=f"API Keys ({len(keys)})")
    table.add_column("ID", style="cyan")
    table.add_column("Nombre", style="bold")
    table.add_column("Prefijo")
    table.add_column("Creada por")
    table.add_column("Último uso")
    for k in keys:
        table.add_row(k.id, k.name, f"{k.key_prefix}...", k.created_by, k.last_used_at or "-")
    console.print(table)


@apikey_app.command("create")
def apikey_create(
    name: str = typer.Argument(..., help="Nombre descriptivo (ej. claude-code, n8n)."),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Crea una nueva API Key (se muestra una sola vez)."""
    svc = _get_service(data_dir)
    try:
        created = svc.create_api_key(name, created_by="cli")
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if as_json:
        _emit(created.model_dump(), True)
    else:
        console.print(f"[green]API Key creada[/green] ({created.name} · {created.id}):")
        console.print(f"[bold]{created.api_key}[/bold]")
        console.print("[yellow]Guardala ahora: no volverá a mostrarse.[/yellow]")


@apikey_app.command("revoke")
def apikey_revoke(
    key_id: str = typer.Argument(..., help="ID de la API Key a revocar."),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Revoca y elimina una API Key existente."""
    svc = _get_service(data_dir)
    try:
        svc.revoke_api_key(key_id, actor="cli")
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if as_json:
        _emit({"deleted": True, "id": key_id}, True)
    else:
        console.print(f"[green]API Key revocada[/green]: {key_id}")


# ----------------------------------------------------------------------
# Insights commands
# ----------------------------------------------------------------------
@insights_app.command("sync")
def insights_sync(
    days: int = typer.Option(7, "--days", help="Ventana de días para métricas de cuenta (1-30)."),
    stories_only: bool = typer.Option(False, "--stories-only", help="Sincronizar solo historias activas (<24h)."),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Sincroniza métricas de cuenta, posts/carruseles e historias activas desde Meta Graph API."""
    svc = _get_service(data_dir)
    try:
        res = svc.sync_insights(days=days, stories_only=stories_only, actor="cli")
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if as_json:
        _emit(res.model_dump(), True)
        return
    console.print(
        f"[green]Insights sincronizados[/green] ({res.synced_at}): "
        f"{res.synced_media_count} posts/reels, {res.synced_stories_count} historias."
    )
    if res.account:
        console.print(
            f"Cuenta @{res.account.username}: {res.account.followers_count} seguidores · "
            f"views({res.period_days}d)={res.account.totals.get('views', '-')} · "
            f"reach({res.period_days}d)={res.account.totals.get('reach', '-')}"
        )
    for err in res.errors:
        console.print(f"[yellow]Aviso:[/yellow] {err}")


@insights_app.command("overview")
def insights_overview(
    days: int = typer.Option(7, "--days", help="Ventana de cuenta a consultar (1-30)."),
    product_type: Optional[str] = typer.Option(None, "--type", help="FEED|STORY|REELS"),
    sort_by: str = typer.Option("published_at", "--sort", help="published_at|views|reach|total_interactions|likes|saved|shares|profile_visits"),
    limit: int = typer.Option(50, "--limit"),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Muestra el resumen de métricas almacenadas de cuenta, posts e historias."""
    svc = _get_service(data_dir)
    ov = svc.get_insights_overview(
        days=days,
        media_product_type=product_type,
        sort_by=sort_by,
        limit=limit,
    )
    if as_json:
        _emit(ov.model_dump(), True)
        return
    if ov.account:
        console.print(
            f"[bold]@{ov.account.username}[/bold] · Seguidores: {ov.account.followers_count} · "
            f"Vistas ({ov.account.period_days}d): {ov.account.totals.get('views', '-')} · "
            f"Alcance: {ov.account.totals.get('reach', '-')}"
        )
    table = Table(title=f"Insights de Contenido ({len(ov.items)})")
    table.add_column("ID", style="cyan")
    table.add_column("Tipo")
    table.add_column("Views", justify="right")
    table.add_column("Reach", justify="right")
    table.add_column("Likes/Repl", justify="right")
    table.add_column("Saved", justify="right")
    table.add_column("Shares", justify="right")
    table.add_column("ER%", justify="right")
    for item in ov.items:
        lr = item.replies if item.media_product_type == "STORY" else item.likes
        table.add_row(
            item.publication_id or item.external_id[-8:],
            f"{item.pub_type or item.media_product_type}{'*' if item.media_product_type == 'STORY' and item.is_active else ''}",
            str(item.views if item.views is not None else "-"),
            str(item.reach if item.reach is not None else "-"),
            str(lr if lr is not None else "-"),
            str(item.saved if item.saved is not None else "-"),
            str(item.shares if item.shares is not None else "-"),
            f"{item.engagement_rate}%" if item.engagement_rate is not None else "-",
        )
    console.print(table)


@insights_app.command("show")
def insights_show(
    identifier: str = typer.Argument(..., help="ID local de publicación o Meta external_id."),
    sync: bool = typer.Option(False, "--sync", help="Actualizar desde Meta antes de mostrar."),
    history: bool = typer.Option(False, "--history", help="Incluir historial de snapshots."),
    as_json: bool = typer.Option(False, "--json"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Muestra las métricas detalladas (y snapshots opcionales) de un post o historia."""
    svc = _get_service(data_dir)
    try:
        item = svc.sync_publication_insights(identifier, actor="cli") if sync else svc.get_media_insight(identifier)
        snaps = svc.get_media_insight_history(identifier) if history else []
    except Exception as exc:
        typer.echo(json.dumps({"error": str(exc)}) if as_json else f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    payload = item.model_dump()
    if history:
        payload["history"] = [s.model_dump() for s in snaps]
    _emit(payload, as_json)


if __name__ == "__main__":
    app()
