# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

import calendar
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

SHIFT_XMLIDS = {
    "M": "almonte_police_extra_services.shift_morning",
    "T": "almonte_police_extra_services.shift_afternoon",
    "N": "almonte_police_extra_services.shift_night",
    "D": "almonte_police_extra_services.shift_rest",
}
CYCLE_PATTERNS = {
    "6_5": ("M", "M", "T", "T", "N", "N", "D", "D", "D", "D", "D"),
    "7_7": ("M",) * 7 + ("D",) * 7,
}
WEEKDAY_PATTERN = ("M", "M", "M", "M", "M", "D", "D")


class PoliceScheduleGenerateWizard(models.TransientModel):
    _name = "almonte.police.schedule.generate.wizard"
    _description = "Generador de cuadrante por ciclos"

    employee_ids = fields.Many2many(
        "hr.employee",
        "almonte_police_schedule_generate_employee_rel",
        "wizard_id",
        "employee_id",
        string="Agentes",
        required=True,
        domain=[("police_category_id", "!=", False)],
        default=lambda self: self._default_employee_ids(),
    )
    pattern = fields.Selection(
        selection=[
            ("6_5", "Ciclo 6-5 continuo (2M - 2T - 2N - 5 libres)"),
            ("5_2", "Ciclo administrativo 5-2 (lunes a viernes de mañana)"),
            ("7_7", "Ciclo 7-7 (7 mañanas - 7 libres)"),
        ],
        string="Ciclo",
        required=True,
        default="6_5",
    )
    date_start = fields.Date(
        string="Fecha de inicio",
        required=True,
        default=fields.Date.context_today,
    )
    cycle_day = fields.Integer(
        string="Día del ciclo en la fecha de inicio",
        required=True,
        default=1,
        help="Posición del ciclo que corresponde a la fecha de inicio "
        "(1 = primer día de mañana). No aplica al ciclo 5-2, que sigue el "
        "día de la semana.",
    )
    scope = fields.Selection(
        selection=[
            ("month", "Hasta fin de mes"),
            ("year", "Hasta fin de año"),
            ("365", "365 días"),
        ],
        string="Alcance",
        required=True,
        default="year",
    )
    date_end = fields.Date(string="Fecha de fin", compute="_compute_date_end")
    preserve_absences = fields.Boolean(
        string="Conservar permisos y bajas",
        default=True,
        help="No sobrescribe los días con vacaciones, bajas, permisos u "
        "otras ausencias ya registradas en el cuadrante.",
    )

    @api.model
    def _default_employee_ids(self):
        if self.env.context.get("active_model") == "hr.employee":
            return self.env["hr.employee"].browse(
                self.env.context.get("active_ids", [])
            )
        return self.env["hr.employee"]

    @api.depends("date_start", "scope")
    def _compute_date_end(self):
        for wizard in self:
            start = wizard.date_start
            if not start:
                wizard.date_end = False
            elif wizard.scope == "month":
                last_day = calendar.monthrange(start.year, start.month)[1]
                wizard.date_end = start.replace(day=last_day)
            elif wizard.scope == "year":
                wizard.date_end = start.replace(month=12, day=31)
            else:
                wizard.date_end = start + timedelta(days=364)

    @api.constrains("pattern", "cycle_day")
    def _check_cycle_day(self):
        for wizard in self:
            length = len(CYCLE_PATTERNS.get(wizard.pattern, WEEKDAY_PATTERN))
            if not 1 <= wizard.cycle_day <= length:
                raise ValidationError(
                    _("El día del ciclo debe estar entre 1 y %s.", length)
                )

    def _get_shift_code(self, date):
        self.ensure_one()
        if self.pattern == "5_2":
            return WEEKDAY_PATTERN[date.weekday()]
        pattern = CYCLE_PATTERNS[self.pattern]
        offset = (date - self.date_start).days + self.cycle_day - 1
        return pattern[offset % len(pattern)]

    def action_generate(self):
        self.ensure_one()
        assignment_model = self.env["almonte.police.schedule.assignment"]
        shifts = {code: self.env.ref(xmlid) for code, xmlid in SHIFT_XMLIDS.items()}
        total_days = (self.date_end - self.date_start).days + 1
        dates = [self.date_start + timedelta(days=day) for day in range(total_days)]
        existing = {
            (assignment.employee_id.id, assignment.date): assignment
            for assignment in assignment_model.search(
                [
                    ("employee_id", "in", self.employee_ids.ids),
                    ("date", ">=", self.date_start),
                    ("date", "<=", self.date_end),
                ]
            )
        }
        to_create = []
        to_update = {code: assignment_model for code in shifts}
        preserved = 0
        for employee in self.employee_ids:
            for date in dates:
                code = self._get_shift_code(date)
                assignment = existing.get((employee.id, date))
                if not assignment:
                    to_create.append(
                        {
                            "employee_id": employee.id,
                            "date": date,
                            "shift_type_id": shifts[code].id,
                        }
                    )
                elif self.preserve_absences and assignment.state_kind == "absence":
                    preserved += 1
                elif assignment.shift_type_id != shifts[code]:
                    to_update[code] |= assignment
        assignment_model.create(to_create)
        for code, assignments in to_update.items():
            assignments.write({"shift_type_id": shifts[code].id})
        updated = sum(len(assignments) for assignments in to_update.values())
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Cuadrante generado"),
                "message": _(
                    "%(created)s días creados, %(updated)s actualizados y "
                    "%(preserved)s ausencias conservadas.",
                    created=len(to_create),
                    updated=updated,
                    preserved=preserved,
                ),
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
