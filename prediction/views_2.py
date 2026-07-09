
import pandas as pd
from django.conf import settings
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render

from desercion_escolar.quality import clamp_prediction_values
from .catalog import ALMACENES, PRODUCTOS, PROVEEDORES, TURNOS
from .model_loader import ModelNotReadyError, get_prediction_model
from .model_storage import model_file_available
from .forms import PrediccionForm
from .models import PrediccionAlmacen

from calendar import monthrange
from datetime import datetime

MESES_DICT = {
    1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril',
    5: 'Mayo', 6: 'Junio', 7: 'Julio', 8: 'Agosto',
    9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre'
}


def _build_feature_maps():
    return {
        'tipo': {'Entrada': 1, 'Salida': 0},
        'almacen': {k: v for v, k in enumerate(ALMACENES)},
        'turno': {k: v for v, k in enumerate(TURNOS)},
        'producto': {k: v for v, k in enumerate(PRODUCTOS)},
        'origen': {k: v for v, k in enumerate(PROVEEDORES)},
    }


def _predict_row(model, maps, cleaned, dia_mes):
    mes = cleaned['mes']
    fecha = datetime(datetime.now().year, mes, dia_mes)
    dia_semana = fecha.weekday()
    x_input = pd.DataFrame([{
        'Tipo': maps['tipo'][cleaned['tipo']],
        'Almacen_llegada': maps['almacen'][cleaned['almacen']],
        'Turno': maps['turno'][cleaned['turno']],
        'Producto': maps['producto'][cleaned['producto']],
        'Origen': maps['origen'][cleaned['origen']],
        'Mes': mes,
        'DiaSemana': dia_semana,
        'Festivo': cleaned['festivo'],
    }])
    prediction = model.predict(x_input)[0]
    values = clamp_prediction_values(prediction)
    return values, dia_semana


@transaction.atomic
def _next_id_carga():
    last = PrediccionAlmacen.objects.order_by('-id_carga').select_for_update().first()
    return (last.id_carga if last else 0) + 1


@login_required
def predecir(request):
    resultado = None
    mensaje = ''
    nombre_mes = ''
    form = PrediccionForm()

    if request.method == 'POST':
        form = PrediccionForm(request.POST)
        if form.is_valid():
            cleaned = form.cleaned_data
            nombre_mes = MESES_DICT.get(cleaned['mes'], 'Desconocido')
            nombre_usuario = request.user.username

            try:
                model = get_prediction_model()
            except ModelNotReadyError as exc:
                messages.error(request, str(exc))
            else:
                maps = _build_feature_maps()
                tipo_prediccion = cleaned['tipo_prediccion']

                if tipo_prediccion == 'unitario':
                    values, _ = _predict_row(model, maps, cleaned, cleaned['dia_mes'])
                    resultado = {
                        'Cantidad_unitaria': values['cantidad_unitaria'],
                        'Bultos': values['bultos'],
                        'PrecioUnidad': round(values['precio_unidad'], 2),
                        'LeadTimeDias': values['lead_time_dias'],
                        'StockAlmacen': values['stock_almacen'],
                    }
                    mensaje = "Predicción del día realizada (no almacenada en BD)."
                    messages.success(request, mensaje)

                elif tipo_prediccion == 'mes_completo':
                    nuevo_id_carga = _next_id_carga()
                    year = datetime.now().year
                    num_days = monthrange(year, cleaned['mes'])[1]
                    registros = []

                    for day in range(1, num_days + 1):
                        values, dia_semana = _predict_row(model, maps, cleaned, day)
                        registros.append(PrediccionAlmacen(
                            id_carga=nuevo_id_carga,
                            tipo=cleaned['tipo'],
                            almacen=cleaned['almacen'],
                            turno=cleaned['turno'],
                            producto=cleaned['producto'],
                            proveedor=cleaned['origen'],
                            mes=cleaned['mes'],
                            dia_mes=day,
                            dia_semana=dia_semana,
                            festivo=bool(cleaned['festivo']),
                            cantidad_unitaria=values['cantidad_unitaria'],
                            bultos=values['bultos'],
                            precio_unidad=values['precio_unidad'],
                            lead_time_dias=values['lead_time_dias'],
                            stock_almacen=values['stock_almacen'],
                            usuario=nombre_usuario,
                        ))

                    PrediccionAlmacen.objects.bulk_create(registros)
                    mensaje = (
                        f"Predicción para mes completo ({nombre_mes}) guardada correctamente. "
                        f"ID de carga: {nuevo_id_carga}"
                    )
                    messages.success(request, mensaje)
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    label = field if field == '__all__' else field.capitalize()
                    messages.error(request, f"{label}: {error}")

    return render(request, 'load_data.html', {
        'productos': PRODUCTOS,
        'proveedores': PROVEEDORES,
        'almacenes': ALMACENES,
        'turnos': TURNOS,
        'resultado': resultado,
        'mes': nombre_mes,
        'mensaje': mensaje,
        'form': form,
    })


@login_required
def home(request):
    return render(request, 'home.html')


def acerca(request):
    return render(request, 'acerca.html')


def health(request):
    """Liveness probe for Railway. Always returns 200 when Django is up."""
    if not model_file_available(settings.MODEL_PATH):
        model_status = "downloading"
    else:
        try:
            get_prediction_model()
            model_status = "ready"
        except ModelNotReadyError:
            model_status = "loading"
    return JsonResponse({"status": "ok", "model": model_status})
