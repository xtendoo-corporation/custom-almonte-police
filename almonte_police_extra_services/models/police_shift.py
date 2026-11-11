# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PoliceShiftType(models.Model):
    _name = "almonte.police.shift.type"
    _description = "Tipo de turno o estado policial"
    _order = "sequence, code"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    alias_codes = fields.Char(
        string="Códigos alternativos",
        help="Otros códigos, separados por comas, que se reconocen como este "
        "turno al importar cuadrantes (p. ej. HRS para Horas sindicales).",
    )
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
    agent_requestable = fields.Boolean(
        string="Solicitable por el agente",
        help="Los agentes pueden solicitar este estado (vacaciones, asuntos "
        "propios...) desde el autoservicio. La solicitud requiere aprobación.",
    )
    extra_block_mode = fields.Selection(
        selection=[
            ("all", "Impide cualquier extra"),
            ("selected", "Impide solo los extras de los turnos indicados"),
            ("none", "No impide extras"),
        ],
        string="Efecto en extras",
        required=True,
        default="all",
        help="Solo aplica a las ausencias. Permite, por ejemplo, que una cita "
        "médica de mañana impida los extras de mañana pero no los de tarde.",
    )
    blocked_extra_shift_ids = fields.Many2many(
        "almonte.police.shift.type",
        "almonte_police_shift_type_blocked_rel",
        "absence_shift_id",
        "blocked_shift_id",
        string="Extras impedidos",
        domain=[("state_kind", "=", "work")],
        help="Turnos de servicio extraordinario que no puede realizar el "
        "agente ese día.",
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

    @api.model
    def _normalize_code(self, code):
        return str(code or "").strip().upper()

    def _get_all_codes(self):
        self.ensure_one()
        aliases = (self.alias_codes or "").split(",")
        return {
            code for code in map(self._normalize_code, [self.code, *aliases]) if code
        }

    @api.model
    def _get_code_map(self):
        """Mapa ``código normalizado -> turno`` incluyendo los alias."""
        code_map = {}
        for shift in self.search([]):
            for code in shift._get_all_codes():
                code_map.setdefault(code, shift)
        return code_map

    @api.constrains("code", "alias_codes")
    def _check_alias_codes(self):
        for record in self:
            others = self.search([("id", "!=", record.id)])
            used = set().union(*(other._get_all_codes() for other in others))
            duplicated = record._get_all_codes() & used
            if duplicated:
                raise ValidationError(
                    _(
                        "Los códigos %s ya se usan en otro tipo de turno.",
                        ", ".join(sorted(duplicated)),
                    )
                )

    def _blocks_extra_shift(self, service_shift):
        """Indica si este estado del cuadrante impide un extra de ``service_shift``."""
        self.ensure_one()
        if self.state_kind != "absence" or self.extra_block_mode == "none":
            return False
        if self.extra_block_mode == "all":
            return True
        return service_shift in self.blocked_extra_shift_ids

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
