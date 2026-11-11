# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    police_baremo_reset_date = fields.Date(
        string="Baremo a cero desde",
        help="Los servicios extraordinarios anteriores a esta fecha no "
        "computan para el orden de adjudicación. El histórico no se borra.",
    )
    police_min_rest_hours = fields.Float(
        string="Descanso mínimo antes de un extra (horas)",
        default=8.0,
        help="Horas libres que debe tener un agente entre el fin de su turno "
        "y el inicio del servicio extraordinario para tener prioridad 2 en "
        "lugar de 'dobla turno'.",
    )

    @api.constrains("police_min_rest_hours")
    def _check_police_min_rest_hours(self):
        for company in self:
            if not 0 <= company.police_min_rest_hours <= 48:
                raise ValidationError(
                    _("El descanso mínimo debe estar entre 0 y 48 horas.")
                )
