# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PoliceShiftType(models.Model):
    _name = "almonte.police.shift.type"
    _description = "Tipo de turno o estado policial"
    _order = "sequence, code"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    sequence = fields.Integer(default=10)
    state_kind = fields.Selection(
        selection=[
            ("work", "Turno trabajado"),
            ("rest", "Descanso"),
            ("absence", "Ausencia"),
            ("other", "Otro"),
        ],
        string="Tipo de estado",
        required=True,
        default="work",
    )
    is_night = fields.Boolean(string="Es noche")
    start_hour = fields.Float(
        string="Hora de inicio",
        help="Hora de inicio del turno. Se usa para calcular las horas libres "
        "antes de un servicio extraordinario.",
    )
    end_hour = fields.Float(
        string="Hora de fin",
        help="Hora de fin del turno. Si es menor o igual que la de inicio, el "
        "turno termina al día siguiente (por ejemplo, noche de 23:00 a 07:00).",
    )
    color = fields.Integer(
        string="Color del calendario",
        default=0,
        help="Color utilizado para identificar este turno en el calendario.",
    )
    active = fields.Boolean(default=True)
    notes = fields.Text()

    _code_unique = models.Constraint(
        "UNIQUE(code)",
        "El código del turno debe ser único.",
    )

    @api.constrains("code")
    def _check_code(self):
        for record in self:
            if not record.code.strip():
                raise ValidationError(_("El código del turno no puede estar vacío."))

    @api.constrains("start_hour", "end_hour")
    def _check_hours(self):
        for record in self:
            if not (0 <= record.start_hour < 24 and 0 <= record.end_hour < 24):
                raise ValidationError(
                    _("Las horas del turno deben estar entre las 00:00 y las 23:59.")
                )

    @api.depends("code", "name", "start_hour", "end_hour", "state_kind")
    @api.depends_context("show_shift_hours")
    def _compute_display_name(self):
        if not self.env.context.get("show_shift_hours"):
            return super()._compute_display_name()
        for shift in self:
            label = f"{shift.code}: {shift.name}"
            if shift.state_kind == "work" and shift.start_hour != shift.end_hour:
                label += (
                    f" ({self._format_hour(shift.start_hour)}"
                    f"-{self._format_hour(shift.end_hour)})"
                )
            shift.display_name = label

    @staticmethod
    def _format_hour(value):
        hours, minutes = divmod(round(value * 60), 60)
        return f"{hours:02d}:{minutes:02d}"

    def _get_interval(self, day_offset=0):
        """Devuelve ``(inicio, fin)`` en horas respecto al día de referencia.

        ``day_offset`` desplaza el turno en días (``-1`` para el día anterior).
        Devuelve ``None`` si el turno no tiene horario configurado.
        """
        self.ensure_one()
        if self.start_hour == self.end_hour:
            return None
        end_hour = self.end_hour
        if end_hour <= self.start_hour:
            end_hour += 24
        offset = day_offset * 24
        return self.start_hour + offset, end_hour + offset


class PoliceShiftRestriction(models.Model):
    _name = "almonte.police.shift.restriction"
    _description = "Restricción entre turnos policiales"

    previous_shift_id = fields.Many2one(
        "almonte.police.shift.type",
        required=True,
        ondelete="cascade",
    )
    required_shift_id = fields.Many2one(
        "almonte.police.shift.type",
        required=True,
        ondelete="cascade",
    )
    reason = fields.Char(required=True, default="Descanso mínimo")
    active = fields.Boolean(default=True)

    _restriction_unique = models.Constraint(
        "UNIQUE(previous_shift_id, required_shift_id)",
        "La restricción entre estos turnos ya existe.",
    )
