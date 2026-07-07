from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models

from desercion_escolar.quality import normalize_email

PHONE_VALIDATOR = RegexValidator(
    regex=r"^[\d\s+\-()]{6,20}$",
    message="Teléfono inválido. Use solo números, espacios, +, - o paréntesis (6-20 caracteres).",
)


class Warehouse(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "Almacén"
        verbose_name_plural = "Almacenes"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Item(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        verbose_name = "Item"
        verbose_name_plural = "Items"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class InventoryLevel(models.Model):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="inventory_levels")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="inventory_levels")
    quantity = models.IntegerField(validators=[MinValueValidator(0)], default=0)
    low_stock_threshold = models.IntegerField(validators=[MinValueValidator(0)], default=0)

    class Meta:
        verbose_name = "Nivel de inventario"
        verbose_name_plural = "Niveles de inventario"
        constraints = [
            models.UniqueConstraint(fields=["warehouse", "item"], name="uniq_inventorylevel_warehouse_item"),
        ]
        ordering = ["warehouse__name", "item__name"]

    def __str__(self) -> str:
        return f"{self.warehouse} - {self.item}: {self.quantity}"


class Customer(models.Model):
    name = models.CharField(max_length=150)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        validators=[PHONE_VALIDATOR],
    )
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["email"],
                condition=models.Q(email__isnull=False) & ~models.Q(email=""),
                name="uniq_customer_email",
            ),
        ]

    def clean(self):
        super().clean()
        if self.name:
            self.name = self.name.strip()
            if not self.name:
                raise ValidationError({"name": "El nombre no puede estar vacío."})
        if self.email:
            self.email = normalize_email(self.email)

    def save(self, *args, **kwargs):
        if self.email:
            self.email = normalize_email(self.email)
        if self.name:
            self.name = self.name.strip()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Sale(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="sales")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="sales")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_created")

    class Meta:
        verbose_name = "Venta"
        verbose_name_plural = "Ventas"
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"Venta #{self.id} - {self.customer}"


class SaleLine(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="sale_lines")
    quantity = models.IntegerField(validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "Línea de venta"
        verbose_name_plural = "Líneas de venta"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["sale", "item"], name="uniq_saleline_sale_item"),
        ]

    def __str__(self) -> str:
        return f"{self.sale_id} - {self.item} x {self.quantity}"


class ScaleReading(models.Model):
    device_id = models.CharField(max_length=50, db_index=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Lectura de balanza"
        verbose_name_plural = "Lecturas de balanza"
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.device_id}: {self.weight_kg} kg"


TIPO_EMPAQUE_CHOICES = [
    ("saco", "Saco"),
    ("caja", "Caja"),
]


class WeightBatch(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Activo"),
        (STATUS_CLOSED, "Cerrado"),
    ]

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="weight_batches",
    )
    device_id = models.CharField(max_length=50, db_index=True)
    tipo_producto = models.CharField(max_length=20, choices=TIPO_EMPAQUE_CHOICES)
    proveedor = models.CharField(max_length=150)
    producto = models.CharField(max_length=150)
    operador = models.CharField(max_length=150)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    started_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Lote de pesaje"
        verbose_name_plural = "Lotes de pesaje"
        ordering = ["-started_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["created_by"],
                condition=models.Q(status="active"),
                name="uniq_active_weightbatch_per_user",
            ),
        ]

    def __str__(self) -> str:
        return f"Lote #{self.id} — {self.proveedor} / {self.producto}"


class WeightRecord(models.Model):
    TURNO_CHOICES = [
        ("mañana", "Mañana"),
        ("tarde", "Tarde"),
        ("noche", "Noche"),
    ]
    TIPO_CHOICES = TIPO_EMPAQUE_CHOICES

    batch = models.ForeignKey(
        WeightBatch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="captures",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="weight_records",
    )
    device_id = models.CharField(max_length=50, db_index=True)
    operador = models.CharField(max_length=150, blank=True, default="")
    turno = models.CharField(max_length=20, choices=TURNO_CHOICES, blank=True, default="mañana")
    tipo_producto = models.CharField(max_length=20, choices=TIPO_CHOICES)
    proveedor = models.CharField(max_length=150)
    producto = models.CharField(max_length=150)
    almacen = models.CharField(max_length=150, blank=True, default="")
    cantidad = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    peso_esperado_kg = models.DecimalField(max_digits=10, decimal_places=3)
    peso_real_kg = models.DecimalField(max_digits=10, decimal_places=3)
    peso_diferencia_kg = models.DecimalField(max_digits=10, decimal_places=3)
    scale_reading = models.ForeignKey(
        ScaleReading,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="weight_records",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Registro de pesaje"
        verbose_name_plural = "Registros de pesaje"
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"Pesaje #{self.id} — {self.device_id} ({self.peso_real_kg} kg)"


class ScaleDeviceState(models.Model):
    device_id = models.CharField(max_length=50, unique=True)
    weight_kg = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    last_reading = models.ForeignKey(
        ScaleReading,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Estado de balanza"
        verbose_name_plural = "Estados de balanza"
        ordering = ["device_id"]

    def __str__(self) -> str:
        return f"{self.device_id}: {self.weight_kg} kg"
