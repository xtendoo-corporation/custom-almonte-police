# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PoliceScheduleAssignment(models.Model):
    _name = "almonte.police.schedule.assignment"
    _description = "Asignación diaria de cuadrante policial"
    _order = "date, employee_id"
    _rec_name = "name"

    name = fields.Char(
        string="Descripción",
        compute="_compute_name",
        store=True,
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Agente",
        required=True,
        ondelete="cascade",
    )
    date = fields.Date(string="Fecha", required=True, index=True)
    shift_type_id = fields.Many2one(
        "almonte.police.shift.type",
        string="Turno / estado",
        required=True,
        ondelete="restrict",
    )
    state_kind = fields.Selection(
        related="shift_type_id.state_kind",
        string="Tipo de estado",
        store=True,
    )
    calendar_color = fields.Integer(
        string="Color del calendario",
        related="shift_type_id.color",
        store=True,
    )
    notes = fields.Text(string="Observaciones")

    _employee_date_unique = models.Constraint(
        "UNIQUE(employee_id, date)",
        "Solo puede existir una asignación de cuadrante por agente y día.",
    )

    @api.model
    def get_for_employee_date(self, employee, date):
        return self.search(
            [("employee_id", "=", employee.id), ("date", "=", date)],
            limit=1,
        )

    @api.depends("employee_id", "date", "shift_type_id")
    def _compute_name(self):
        for record in self:
            if record.employee_id and record.shift_type_id and record.date:
                record.name = (
                    f"{record.employee_id.name} · {record.shift_type_id.name} · "
                    f"{record.date.strftime('%d/%m/%Y')}"
                )
            else:
                record.name = _("Nueva asignación de cuadrante")

    def get_previous_shift(self):
        self.ensure_one()
        previous_date = self.date - timedelta(days=1)
        return self.get_for_employee_date(self.employee_id, previous_date).shift_type_id

    @api.constrains("employee_id")
    def _check_employee_is_police(self):
        for record in self:
            if not record.employee_id.police_category:
                raise ValidationError(
                    _("El agente debe tener informada la categoría policial.")
                )
