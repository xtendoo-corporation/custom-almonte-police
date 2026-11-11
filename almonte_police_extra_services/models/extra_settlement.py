# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

MONTHS = [
    ("1", "Enero"),
    ("2", "Febrero"),
    ("3", "Marzo"),
    ("4", "Abril"),
    ("5", "Mayo"),
    ("6", "Junio"),
    ("7", "Julio"),
    ("8", "Agosto"),
    ("9", "Septiembre"),
    ("10", "Octubre"),
    ("11", "Noviembre"),
    ("12", "Diciembre"),
]


class PoliceExtraSettlement(models.Model):
    _name = "almonte.police.extra.settlement"
    _description = "Liquidación de servicios extraordinarios"
    _inherit = "mail.thread"
    _order = "date_from desc, period_type, id desc"

    name = fields.Char(string="Liquidación", compute="_compute_name", store=True)
    period_type = fields.Selection(
        selection=[("month", "Por mes"), ("year", "Por año completo")],
        string="Frecuencia",
        required=True,
        default="month",
        tracking=True,
    )
    month = fields.Selection(
        selection=MONTHS,
        string="Mes",
        default=lambda self: str(fields.Date.context_today(self).month),
        tracking=True,
    )
    year = fields.Integer(
        string="Año",
        required=True,
        default=lambda self: fields.Date.context_today(self).year,
        tracking=True,
    )
    scope = fields.Selection(
        selection=[("all", "Plantilla completa"), ("employee", "Un policía")],
        string="Ámbito",
        required=True,
        default="all",
        tracking=True,
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Policía",
        domain="[('police_category_id', '!=', False)]",
        tracking=True,
    )
    date_from = fields.Date(
        string="Desde", compute="_compute_period", store=True, index=True
    )
    date_to = fields.Date(string="Hasta", compute="_compute_period", store=True)
    state = fields.Selection(
        selection=[("draft", "Borrador"), ("confirmed", "Confirmada")],
        string="Estado",
        required=True,
        default="draft",
        copy=False,
        tracking=True,
    )
    generated_on = fields.Datetime(string="Generada el", readonly=True, copy=False)
    line_ids = fields.One2many(
        "almonte.police.extra.settlement.line",
        "settlement_id",
        string="Liquidación por policía",
        copy=False,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    employee_count = fields.Integer(
        string="Policías", compute="_compute_totals", store=True
    )
    total_services = fields.Integer(
        string="Servicios", compute="_compute_totals", store=True
    )
    total_days = fields.Float(
        string="Días de servicio", compute="_compute_totals", store=True
    )
    total_amount = fields.Monetary(
        string="Importe total", compute="_compute_totals", store=True
    )
    notes = fields.Text(string="Observaciones")

    _period_scope_unique = models.UniqueIndex(
        "(period_type, COALESCE(month, '0'), year, scope, "
        "COALESCE(employee_id, 0), company_id)",
        "Ya existe una liquidación para ese periodo y ámbito.",
    )

    @api.depends("period_type", "month", "year", "scope", "employee_id")
    def _compute_name(self):
        month_names = dict(MONTHS)
        for settlement in self:
            period = (
                f"{month_names.get(settlement.month, '')} {settlement.year}"
                if settlement.period_type == "month"
                else str(settlement.year)
            )
            target = (
                settlement.employee_id.name
                if settlement.scope == "employee"
                else _("Plantilla completa")
            )
            settlement.name = _(
                "Liquidación %(period)s - %(target)s",
                period=period,
                target=target or "",
            )

    @api.depends("period_type", "month", "year")
    def _compute_period(self):
        for settlement in self:
            if not settlement.year or (
                settlement.period_type == "month" and not settlement.month
            ):
                settlement.date_from = settlement.date_to = False
                continue
            if settlement.period_type == "year":
                date_from = date(settlement.year, 1, 1)
                date_to = date(settlement.year, 12, 31)
            else:
                date_from = date(settlement.year, int(settlement.month), 1)
                date_to = date_from + relativedelta(months=1, days=-1)
            settlement.date_from = date_from
            settlement.date_to = date_to

    @api.depends(
        "line_ids.service_count", "line_ids.total_days", "line_ids.total_amount"
    )
    def _compute_totals(self):
        for settlement in self:
            lines = settlement.line_ids
            settlement.employee_count = len(lines)
            settlement.total_services = sum(lines.mapped("service_count"))
            settlement.total_days = sum(lines.mapped("total_days"))
            settlement.total_amount = sum(lines.mapped("total_amount"))

    @api.constrains("period_type", "month", "year", "scope", "employee_id")
    def _check_period_and_scope(self):
        for settlement in self:
            if settlement.period_type == "month" and not settlement.month:
                raise ValidationError(_("Indica el mes de la liquidación."))
            if not 2000 <= settlement.year <= 2100:
                raise ValidationError(_("El año de la liquidación no es válido."))
            if settlement.scope == "employee" and not settlement.employee_id:
                raise ValidationError(_("Indica el policía a liquidar."))

    @api.onchange("scope")
    def _onchange_scope(self):
        if self.scope == "all":
            self.employee_id = False

    @api.onchange("period_type")
    def _onchange_period_type(self):
        if self.period_type == "year":
            self.month = False

    def _get_history_domain(self):
        self.ensure_one()
        domain = [
            ("service_date", ">=", self.date_from),
            ("service_date", "<=", self.date_to),
        ]
        if self.scope == "employee":
            domain.append(("employee_id", "=", self.employee_id.id))
        return domain

    def action_generate(self):
        history_model = self.env["almonte.police.extra.service.history"]
        for settlement in self:
            if settlement.state != "draft":
                raise UserError(
                    _("Solo se pueden regenerar liquidaciones en borrador.")
                )
            histories = history_model.search(settlement._get_history_domain())
            line_commands = [fields.Command.clear()]
            for employee in histories.employee_id.sorted(
                lambda employee: (employee.police_employee_id or 0, employee.name)
            ):
                employee_histories = histories.filtered(
                    lambda history, employee=employee: history.employee_id == employee
                )
                line_commands.append(
                    fields.Command.create(
                        {
                            "employee_id": employee.id,
                            "police_category_id": employee.police_category_id.id,
                            "police_group_id": employee.police_group_id.id,
                            "history_ids": [fields.Command.set(employee_histories.ids)],
                            "service_count": len(employee_histories),
                            "total_days": sum(employee_histories.mapped("days")),
                            "total_hours": sum(employee_histories.mapped("hours")),
                            "total_amount": sum(employee_histories.mapped("amount")),
                        }
                    )
                )
            settlement.write(
                {"line_ids": line_commands, "generated_on": fields.Datetime.now()}
            )
        return True

    def action_confirm(self):
        for settlement in self:
            if settlement.state != "draft":
                continue
            if not settlement.generated_on:
                settlement.action_generate()
            settlement.state = "confirmed"
        return True

    def action_draft(self):
        self.write({"state": "draft"})
        return True

    def action_print_pdf(self):
        return self.env.ref(
            "almonte_police_extra_services.action_report_extra_settlement"
        ).report_action(self)

    def action_export_xlsx(self):
        return self.env.ref(
            "almonte_police_extra_services.action_report_extra_settlement_xlsx"
        ).report_action(self)

    def unlink(self):
        if any(settlement.state == "confirmed" for settlement in self):
            raise UserError(_("No se puede eliminar una liquidación confirmada."))
        return super().unlink()

    @api.model
    def _cron_generate_previous_month(self):
        """Genera (o actualiza si sigue en borrador) la liquidación mensual
        de la plantilla completa correspondiente al mes anterior."""
        previous_month = fields.Date.context_today(self) - relativedelta(months=1)
        values = {
            "period_type": "month",
            "month": str(previous_month.month),
            "year": previous_month.year,
            "scope": "all",
            "company_id": self.env.company.id,
        }
        settlement = self.search(
            [(field, "=", value) for field, value in values.items()]
            + [("employee_id", "=", False)],
            limit=1,
        ) or self.create(values)
        if settlement.state == "draft":
            settlement.action_generate()
        return settlement


class PoliceExtraSettlementLine(models.Model):
    _name = "almonte.police.extra.settlement.line"
    _description = "Línea de liquidación de servicios extraordinarios"
    _order = "settlement_id, police_employee_id, employee_id"

    settlement_id = fields.Many2one(
        "almonte.police.extra.settlement",
        required=True,
        ondelete="cascade",
        index=True,
    )
    employee_id = fields.Many2one(
        "hr.employee", string="Policía", required=True, ondelete="restrict"
    )
    settlement_name = fields.Char(
        related="settlement_id.name", string="Liquidación", store=True
    )
    date_from = fields.Date(related="settlement_id.date_from", store=True)
    date_to = fields.Date(related="settlement_id.date_to", store=True)
    settlement_state = fields.Selection(
        related="settlement_id.state", string="Estado", store=True
    )
    identification_id = fields.Char(
        related="employee_id.identification_id", string="DNI"
    )
    police_employee_id = fields.Integer(
        related="employee_id.police_employee_id", string="ID policía", store=True
    )
    police_category_id = fields.Many2one(
        "almonte.police.category", string="Categoría", ondelete="restrict"
    )
    police_group_id = fields.Many2one(
        "almonte.police.group", string="Grupo", ondelete="restrict"
    )
    history_ids = fields.Many2many(
        "almonte.police.extra.service.history",
        "almonte_police_settlement_line_history_rel",
        "line_id",
        "history_id",
        string="Servicios realizados",
    )
    service_count = fields.Integer(string="Servicios")
    total_days = fields.Float(string="Días de servicio")
    total_hours = fields.Float(string="Horas")
    currency_id = fields.Many2one(related="settlement_id.currency_id")
    total_amount = fields.Monetary(string="Importe")
