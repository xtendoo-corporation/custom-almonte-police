# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models


class PoliceServiceType(models.Model):
    _name = "almonte.police.service.type"
    _description = "Tipo de servicio extraordinario"
    _order = "name"

    name = fields.Char(string="Nombre", required=True, translate=True)
    code = fields.Char(string="Código", required=True, index=True)
    pricing_mode = fields.Selection(
        selection=[
            ("fixed", "Importe fijo"),
            ("hourly", "Por horas"),
        ],
        required=True,
        default="fixed",
    )
    unit_price = fields.Monetary(
        string="Tarifa",
        required=True,
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Moneda",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    active = fields.Boolean(string="Activo", default=True)
    notes = fields.Text(string="Observaciones")

    _code_unique = models.Constraint(
        "UNIQUE(code)",
        "El código del servicio debe ser único.",
    )
