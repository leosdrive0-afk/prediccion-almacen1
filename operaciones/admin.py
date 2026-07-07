from django.contrib import admin

from .models import (
    Customer,
    InventoryLevel,
    Item,
    Sale,
    SaleLine,
    ScaleDeviceState,
    ScaleReading,
    Warehouse,
    WeightBatch,
    WeightRecord,
)


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    search_fields = ["name"]
    list_display = ["name"]


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    search_fields = ["name"]
    list_display = ["name"]


@admin.register(InventoryLevel)
class InventoryLevelAdmin(admin.ModelAdmin):
    search_fields = ["warehouse__name", "item__name"]
    list_filter = ["warehouse"]
    list_display = ["warehouse", "item", "quantity", "low_stock_threshold"]


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    search_fields = ["name", "email", "phone"]
    list_filter = ["active"]
    list_display = ["name", "email", "phone", "active"]


class SaleLineInline(admin.TabularInline):
    model = SaleLine
    extra = 0


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_filter = ["warehouse", "created_at"]
    search_fields = ["customer__name", "created_by__username"]
    list_display = ["id", "created_at", "customer", "warehouse", "created_by"]
    inlines = [SaleLineInline]


@admin.register(ScaleReading)
class ScaleReadingAdmin(admin.ModelAdmin):
    list_filter = ["device_id", "created_at"]
    search_fields = ["device_id"]
    list_display = ["id", "device_id", "weight_kg", "created_at"]
    readonly_fields = ["device_id", "weight_kg", "created_at"]


@admin.register(ScaleDeviceState)
class ScaleDeviceStateAdmin(admin.ModelAdmin):
    search_fields = ["device_id"]
    list_display = ["device_id", "weight_kg", "last_reading", "updated_at"]


@admin.register(WeightRecord)
class WeightRecordAdmin(admin.ModelAdmin):
    list_filter = ["turno", "tipo_producto", "device_id", "created_at", "batch"]
    search_fields = ["proveedor", "producto", "operador", "device_id", "created_by__username"]
    list_display = [
        "id",
        "batch",
        "device_id",
        "proveedor",
        "producto",
        "peso_real_kg",
        "created_by",
        "created_at",
    ]
    readonly_fields = ["created_at"]


@admin.register(WeightBatch)
class WeightBatchAdmin(admin.ModelAdmin):
    list_filter = ["status", "tipo_producto", "device_id", "started_at"]
    search_fields = ["proveedor", "producto", "operador", "device_id", "created_by__username"]
    list_display = [
        "id",
        "device_id",
        "proveedor",
        "producto",
        "status",
        "created_by",
        "started_at",
        "closed_at",
    ]
    readonly_fields = ["started_at"]
