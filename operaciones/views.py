from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction, IntegrityError
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render

from desercion_escolar.quality import aggregate_quantities_by_item
from .forms import CustomerForm, InventoryAdjustForm
from .forms import SaleForm, SaleLineFormSet, SALELINE_PREFIX
from .models import Customer, InventoryLevel, Sale, Warehouse, WeightRecord


def _add_form_errors_to_messages(request, form):
    for field, errors in form.errors.items():
        for error in errors:
            if field == "__all__":
                messages.error(request, error)
            else:
                messages.error(request, f"{field.capitalize()}: {error}")


@login_required
def clientes_list(request):
    q = (request.GET.get("q") or "").strip()
    customers = Customer.objects.all()
    if q:
        customers = customers.filter(name__icontains=q)
    customers = customers.order_by("name")
    return render(request, "operaciones/clientes_list.html", {"customers": customers, "q": q})


@login_required
def clientes_create(request):
    if request.method == "POST":
        form = CustomerForm(request.POST)
        if form.is_valid():
            try:
                form.save()
            except IntegrityError:
                messages.error(request, "Ya existe un cliente con ese correo.")
            else:
                messages.success(request, "Cliente creado correctamente.")
                return redirect("operaciones:clientes_list")
        else:
            _add_form_errors_to_messages(request, form)
    else:
        form = CustomerForm()

    return render(request, "operaciones/clientes_form.html", {"form": form})


@login_required
def inventario_list(request):
    warehouse_id = request.GET.get("warehouse")
    warehouses = Warehouse.objects.all().order_by("name")
    selected_warehouse = None

    levels = InventoryLevel.objects.select_related("warehouse", "item")
    if warehouse_id:
        selected_warehouse = get_object_or_404(Warehouse, id=warehouse_id)
        levels = levels.filter(warehouse=selected_warehouse)
    levels = levels.order_by("warehouse__name", "item__name")

    return render(
        request,
        "operaciones/inventario_list.html",
        {
            "warehouses": warehouses,
            "selected_warehouse": selected_warehouse,
            "levels": levels,
        },
    )


@login_required
def inventario_adjust(request):
    if request.method == "POST":
        form = InventoryAdjustForm(request.POST)
        if form.is_valid():
            warehouse = form.cleaned_data["warehouse"]
            item = form.cleaned_data["item"]
            delta = int(form.cleaned_data["delta"])
            threshold = form.cleaned_data.get("low_stock_threshold")

            with transaction.atomic():
                level, _ = InventoryLevel.objects.select_for_update().get_or_create(
                    warehouse=warehouse,
                    item=item,
                    defaults={"quantity": 0, "low_stock_threshold": threshold or 0},
                )

                if delta != 0 and level.quantity + delta < 0:
                    form.add_error("delta", "No puedes dejar el stock en negativo.")
                else:
                    if delta != 0:
                        InventoryLevel.objects.filter(id=level.id).update(
                            quantity=F("quantity") + delta
                        )
                    if threshold is not None:
                        level.low_stock_threshold = threshold
                        level.save(update_fields=["low_stock_threshold"])

                    messages.success(request, "Inventario ajustado correctamente.")
                    return redirect("operaciones:inventario_list")
    else:
        form = InventoryAdjustForm()

    return render(request, "operaciones/inventario_adjust.html", {"form": form})


@login_required
def inventario_alertas(request):
    alerts = (
        InventoryLevel.objects.select_related("warehouse", "item")
        .filter(low_stock_threshold__gt=0, quantity__lte=F("low_stock_threshold"))
        .order_by("warehouse__name", "item__name")
    )
    return render(request, "operaciones/inventario_alertas.html", {"alerts": alerts})


@login_required
def ventas_list(request):
    sales = (
        Sale.objects.select_related("customer", "warehouse", "created_by")
        .order_by("-created_at", "-id")[:200]
    )
    return render(request, "operaciones/ventas_list.html", {"sales": sales})


@login_required
def ventas_create(request):
    if request.method == "POST":
        form = SaleForm(request.POST)
        if form.is_valid():
            sale = form.save(commit=False)
            sale.created_by = request.user

            formset = SaleLineFormSet(request.POST, instance=sale, prefix=SALELINE_PREFIX)
            if formset.is_valid():
                lines = []
                for f in formset.forms:
                    cd = getattr(f, "cleaned_data", None) or {}
                    if cd.get("DELETE"):
                        continue
                    item = cd.get("item")
                    qty = cd.get("quantity")
                    if item and qty:
                        line = f.save(commit=False)
                        lines.append(line)

                if not lines:
                    formset._non_form_errors.append("Agrega al menos una línea de venta.")
                else:
                    qty_by_item, item_names = aggregate_quantities_by_item(lines)

                    with transaction.atomic():
                        levels = (
                            InventoryLevel.objects.select_for_update()
                            .select_related("item")
                            .filter(warehouse=sale.warehouse, item_id__in=qty_by_item.keys())
                        )
                        levels_by_item = {lvl.item_id: lvl for lvl in levels}

                        stock_ok = True
                        for item_id, total_qty in qty_by_item.items():
                            lvl = levels_by_item.get(item_id)
                            item = item_names[item_id]
                            if lvl is None:
                                formset._non_form_errors.append(
                                    f"Falta inventario para {item.name} "
                                    f"(ejecuta: python manage.py seed_operaciones)."
                                )
                                stock_ok = False
                            elif lvl.quantity < total_qty:
                                formset._non_form_errors.append(
                                    f"Stock insuficiente para {item.name}. "
                                    f"Disponible: {lvl.quantity}, solicitado: {total_qty}."
                                )
                                stock_ok = False

                        if stock_ok:
                            sale.save()
                            merged_lines = {}
                            for line in lines:
                                if line.item_id in merged_lines:
                                    merged_lines[line.item_id].quantity += line.quantity
                                else:
                                    merged_lines[line.item_id] = line

                            for line in merged_lines.values():
                                line.sale = sale
                                line.save()

                            for item_id, total_qty in qty_by_item.items():
                                InventoryLevel.objects.filter(
                                    warehouse=sale.warehouse, item_id=item_id
                                ).update(quantity=F("quantity") - total_qty)

                            messages.success(request, f"Venta #{sale.id} creada.")
                            return redirect("operaciones:ventas_detail", sale_id=sale.id)
        else:
            formset = SaleLineFormSet(request.POST, prefix=SALELINE_PREFIX)
    else:
        form = SaleForm()
        sale = Sale(created_by=request.user)
        formset = SaleLineFormSet(instance=sale, prefix=SALELINE_PREFIX)

    return render(request, "operaciones/ventas_form.html", {"form": form, "formset": formset})


@login_required
def ventas_detail(request, sale_id: int):
    sale = get_object_or_404(
        Sale.objects.select_related("customer", "warehouse", "created_by").prefetch_related("lines__item"),
        id=sale_id,
    )
    return render(request, "operaciones/ventas_detail.html", {"sale": sale})


@login_required
def peso_create(request):
    return render(request, "operaciones/peso_create.html")


@login_required
def peso_list(request):
    records = (
        WeightRecord.objects.filter(created_by=request.user)
        .select_related("batch")
        .order_by("-created_at", "-id")[:200]
    )
    return render(request, "operaciones/peso_list.html", {"records": records})


@login_required
def peso_detail(request, record_id: int):
    record = get_object_or_404(
        WeightRecord.objects.select_related("created_by", "scale_reading", "batch"),
        id=record_id,
        created_by=request.user,
    )
    return render(request, "operaciones/peso_detail.html", {"record": record})


@login_required
def peso_mockup(request):
    return redirect("operaciones:peso_create")
