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
