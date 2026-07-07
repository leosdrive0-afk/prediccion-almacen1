import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .iot_auth import require_iot_api_key
from .models import ScaleDeviceState, ScaleReading, WeightBatch, WeightRecord

TURNO_VALUES = {"mañana", "tarde", "noche"}
TIPO_VALUES = {"saco", "caja"}


def _parse_weight(value) -> Decimal | None:
    try:
        weight = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if weight < 0:
        return None
    return weight


def _json_error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"success": False, "error": message}, status=status)


@csrf_exempt
@require_POST
@require_iot_api_key
def iot_peso_lectura(request):
    """Recibe lecturas POST desde dispositivos IoT (balanzas)."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return _json_error("JSON inválido.")

    if not isinstance(body, dict):
        return _json_error("El cuerpo debe ser un objeto JSON.")

    device_id = (body.get("deviceId") or "").strip()
    if not device_id:
        return _json_error("Campo deviceId requerido.")

    if len(device_id) > 50:
        return _json_error("deviceId demasiado largo (máx. 50 caracteres).")

    if "weightKg" not in body:
        return _json_error("Campo weightKg requerido.")

    weight_kg = _parse_weight(body.get("weightKg"))
    if weight_kg is None:
        return _json_error("weightKg debe ser un número mayor o igual a 0.")

    with transaction.atomic():
        reading = ScaleReading.objects.create(device_id=device_id, weight_kg=weight_kg)
        state, created = ScaleDeviceState.objects.select_for_update().get_or_create(
            device_id=device_id,
            defaults={"weight_kg": weight_kg, "last_reading": reading},
        )
        if not created:
            state.weight_kg = weight_kg
            state.last_reading = reading
            state.save(update_fields=["weight_kg", "last_reading", "updated_at"])

    return JsonResponse(
        {
            "success": True,
            "deviceId": device_id,
            "weightKg": float(weight_kg),
            "readingId": reading.id,
        },
        status=201,
    )


@login_required
@require_GET
def peso_live(request):
    """Devuelve el estado actual de las balanzas (polling desde la UI)."""
    since_id = request.GET.get("since")
    device_id = (request.GET.get("deviceId") or "").strip()

    states = ScaleDeviceState.objects.select_related("last_reading")
    if device_id:
        states = states.filter(device_id=device_id)

    devices = []
    latest_id = 0
    for state in states:
        reading = state.last_reading
        reading_id = reading.id if reading else 0
        latest_id = max(latest_id, reading_id)
        devices.append(
            {
                "deviceId": state.device_id,
                "weightKg": float(state.weight_kg),
                "readingId": reading_id or None,
                "updatedAt": state.updated_at.isoformat(),
            }
        )

    has_new = False
    if since_id:
        try:
            since_id_int = int(since_id)
            has_new = ScaleReading.objects.filter(id__gt=since_id_int).exists()
        except (TypeError, ValueError):
            has_new = False

    return JsonResponse(
        {
            "success": True,
            "devices": devices,
            "latestReadingId": latest_id,
            "hasNew": has_new,
        }
    )


@login_required
@require_POST
def peso_reiniciar(request):
    """Reinicia el peso mostrado de una balanza."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return _json_error("JSON inválido.")

    device_id = (body.get("deviceId") or "").strip()
    if not device_id:
        return _json_error("Campo deviceId requerido.")

    with transaction.atomic():
        state, _ = ScaleDeviceState.objects.select_for_update().get_or_create(
            device_id=device_id,
            defaults={"weight_kg": Decimal("0")},
        )
        state.weight_kg = Decimal("0")
        state.last_reading = None
        state.save(update_fields=["weight_kg", "last_reading", "updated_at"])

    return JsonResponse(
        {
            "success": True,
            "deviceId": device_id,
            "weightKg": 0.0,
        }
    )


def _expected_weight(cantidad: int, tipo_producto: str) -> Decimal:
    peso_por_unidad = Decimal("0.8") if tipo_producto == "caja" else Decimal("1")
    return Decimal(cantidad) * peso_por_unidad


def _get_active_batch(user) -> WeightBatch | None:
    return (
        WeightBatch.objects.filter(created_by=user, status=WeightBatch.STATUS_ACTIVE)
        .order_by("-started_at", "-id")
        .first()
    )


def _batch_to_dict(batch: WeightBatch) -> dict:
    return {
        "batchId": batch.id,
        "deviceId": batch.device_id,
        "tipoProducto": batch.tipo_producto,
        "proveedor": batch.proveedor,
        "producto": batch.producto,
        "operador": batch.operador,
        "startedAt": batch.started_at.isoformat(),
        "captureCount": batch.captures.count(),
    }


def _capture_to_dict(record: WeightRecord, sequence: int | None = None) -> dict:
    data = {
        "recordId": record.id,
        "pesoRealKg": float(record.peso_real_kg),
        "pesoEsperadoKg": float(record.peso_esperado_kg),
        "pesoDiferenciaKg": float(record.peso_diferencia_kg),
        "readingId": record.scale_reading_id,
        "createdAt": record.created_at.isoformat(),
        "detailUrl": f"/operaciones/peso/registros/{record.id}/",
    }
    if sequence is not None:
        data["sequence"] = sequence
    return data


def _create_capture_from_batch(batch: WeightBatch, user) -> tuple[WeightRecord | None, str | None]:
    state = (
        ScaleDeviceState.objects.filter(device_id=batch.device_id)
        .select_related("last_reading")
        .first()
    )
    if not state or state.weight_kg <= 0 or not state.last_reading:
        return None, (
            "No hay lectura IoT disponible para esta balanza. "
            "Envíe el peso desde el dispositivo antes de capturar."
        )

    reading = state.last_reading
    if WeightRecord.objects.filter(batch=batch, scale_reading=reading).exists():
        return None, "Esta lectura IoT ya fue registrada en el lote actual."

    cantidad = 1
    peso_esperado = _expected_weight(cantidad, batch.tipo_producto)
    peso_real = state.weight_kg
    peso_diferencia = peso_real - peso_esperado

    record = WeightRecord.objects.create(
        batch=batch,
        created_by=user,
        device_id=batch.device_id,
        operador=batch.operador,
        turno="mañana",
        tipo_producto=batch.tipo_producto,
        proveedor=batch.proveedor,
        producto=batch.producto,
        almacen="",
        cantidad=cantidad,
        peso_esperado_kg=peso_esperado,
        peso_real_kg=peso_real,
        peso_diferencia_kg=peso_diferencia,
        scale_reading=reading,
    )
    return record, None


@login_required
@require_GET
def peso_lote_activo(request):
    batch = _get_active_batch(request.user)
    if not batch:
        return JsonResponse({"success": True, "batch": None, "captures": []})

    captures = list(batch.captures.order_by("created_at", "id"))
    return JsonResponse(
        {
            "success": True,
            "batch": _batch_to_dict(batch),
            "captures": [_capture_to_dict(r, i + 1) for i, r in enumerate(captures)],
        }
    )


@login_required
@require_POST
def peso_lote_iniciar(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return _json_error("JSON inválido.")

    device_id = (body.get("deviceId") or "").strip()
    tipo_producto = (body.get("tipoProducto") or "").strip()
    proveedor = (body.get("proveedor") or "").strip()
    producto = (body.get("producto") or "").strip()

    if not device_id:
        return _json_error("Seleccione una balanza.")
    if tipo_producto not in TIPO_VALUES:
        return _json_error("Tipo de empaque inválido.")
    if not proveedor:
        return _json_error("Campo proveedor requerido.")
    if not producto:
        return _json_error("Campo producto requerido.")

    operador = (body.get("operador") or "").strip()
    if not operador:
        operador = request.user.get_full_name() or request.user.username

    with transaction.atomic():
        active = _get_active_batch(request.user)
        if active:
            active.status = WeightBatch.STATUS_CLOSED
            active.closed_at = timezone.now()
            active.save(update_fields=["status", "closed_at"])

        batch = WeightBatch.objects.create(
            created_by=request.user,
            device_id=device_id,
            tipo_producto=tipo_producto,
            proveedor=proveedor,
            producto=producto,
            operador=operador,
        )

    return JsonResponse({"success": True, "batch": _batch_to_dict(batch)}, status=201)


@login_required
@require_POST
def peso_lote_capturar(request):
    batch = _get_active_batch(request.user)
    if not batch:
        return _json_error("No hay un lote activo. Configure el lote primero.", status=404)

    with transaction.atomic():
        batch = WeightBatch.objects.select_for_update().get(pk=batch.pk)
        record, error = _create_capture_from_batch(batch, request.user)
        if error:
            return _json_error(error)

    sequence = batch.captures.filter(created_at__lte=record.created_at, id__lte=record.id).count()
    return JsonResponse(
        {
            "success": True,
            "batch": _batch_to_dict(batch),
            "capture": _capture_to_dict(record, sequence),
        },
        status=201,
    )


@login_required
@require_POST
def peso_lote_cerrar(request):
    batch = _get_active_batch(request.user)
    if not batch:
        return _json_error("No hay un lote activo.", status=404)

    batch.status = WeightBatch.STATUS_CLOSED
    batch.closed_at = timezone.now()
    batch.save(update_fields=["status", "closed_at"])

    captures = list(batch.captures.order_by("created_at", "id"))
    return JsonResponse(
        {
            "success": True,
            "batch": _batch_to_dict(batch),
            "captures": [_capture_to_dict(r, i + 1) for i, r in enumerate(captures)],
            "totalCaptures": len(captures),
        }
    )


@login_required
@require_POST
def peso_registrar(request):
    """Registra un pesaje completo asociado al usuario autenticado."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return _json_error("JSON inválido.")

    device_id = (body.get("deviceId") or "").strip()
    operador = (body.get("operador") or "").strip()
    turno = (body.get("turno") or "").strip()
    tipo_producto = (body.get("tipoProducto") or "").strip()
    proveedor = (body.get("proveedor") or body.get("cliente") or "").strip()
    producto = (body.get("producto") or "").strip()
    almacen = (body.get("almacen") or "").strip()

    if not device_id:
        return _json_error("Campo deviceId requerido.")
    if not operador:
        return _json_error("Campo operador requerido.")
    if turno not in TURNO_VALUES:
        return _json_error("Turno inválido.")
    if tipo_producto not in TIPO_VALUES:
        return _json_error("Tipo de producto inválido.")
    if not proveedor:
        return _json_error("Campo proveedor requerido.")
    if not producto:
        return _json_error("Campo producto requerido.")

    try:
        cantidad = int(body.get("cantidad"))
    except (TypeError, ValueError):
        return _json_error("Cantidad inválida.")
    if cantidad < 1:
        return _json_error("La cantidad debe ser al menos 1.")

    peso_esperado = _expected_weight(cantidad, tipo_producto)

    state = ScaleDeviceState.objects.filter(device_id=device_id).select_related("last_reading").first()
    if not state or state.weight_kg <= 0 or not state.last_reading:
        return _json_error(
            "No hay lectura IoT disponible para esta balanza. "
            "Envíe el peso desde el dispositivo antes de registrar."
        )

    peso_real = state.weight_kg
    peso_diferencia = peso_real - peso_esperado

    record = WeightRecord.objects.create(
        created_by=request.user,
        device_id=device_id,
        operador=operador,
        turno=turno,
        tipo_producto=tipo_producto,
        proveedor=proveedor,
        producto=producto,
        almacen=almacen,
        cantidad=cantidad,
        peso_esperado_kg=peso_esperado,
        peso_real_kg=peso_real,
        peso_diferencia_kg=peso_diferencia,
        scale_reading=state.last_reading,
    )

    return JsonResponse(
        {
            "success": True,
            "recordId": record.id,
            "deviceId": device_id,
            "pesoEsperadoKg": float(peso_esperado),
            "pesoRealKg": float(peso_real),
            "pesoDiferenciaKg": float(peso_diferencia),
            "detailUrl": f"/operaciones/peso/registros/{record.id}/",
        },
        status=201,
    )
