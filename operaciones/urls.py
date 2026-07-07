from django.urls import path

from . import peso_api, views

app_name = "operaciones"

urlpatterns = [
    # Clientes
    path("clientes/", views.clientes_list, name="clientes_list"),
    path("clientes/nuevo/", views.clientes_create, name="clientes_create"),
    # Inventario
    path("inventario/", views.inventario_list, name="inventario_list"),
    path("inventario/ajustar/", views.inventario_adjust, name="inventario_adjust"),
    path("inventario/alertas/", views.inventario_alertas, name="inventario_alertas"),
    # Ventas
    path("ventas/", views.ventas_list, name="ventas_list"),
    path("ventas/nueva/", views.ventas_create, name="ventas_create"),
    path("ventas/<int:sale_id>/", views.ventas_detail, name="ventas_detail"),
    # Peso
    path("peso/", views.peso_create, name="peso_create"),
    path("peso/mockup/", views.peso_mockup, name="peso_mockup"),
    path("peso/live/", peso_api.peso_live, name="peso_live"),
    path("peso/reiniciar/", peso_api.peso_reiniciar, name="peso_reiniciar"),
    path("peso/registrar/", peso_api.peso_registrar, name="peso_registrar"),
    path("peso/lote/activo/", peso_api.peso_lote_activo, name="peso_lote_activo"),
    path("peso/lote/iniciar/", peso_api.peso_lote_iniciar, name="peso_lote_iniciar"),
    path("peso/lote/capturar/", peso_api.peso_lote_capturar, name="peso_lote_capturar"),
    path("peso/lote/cerrar/", peso_api.peso_lote_cerrar, name="peso_lote_cerrar"),
    path("peso/registros/", views.peso_list, name="peso_list"),
    path("peso/registros/<int:record_id>/", views.peso_detail, name="peso_detail"),
]

