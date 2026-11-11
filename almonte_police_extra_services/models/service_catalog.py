# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, api, fields, models


class PoliceServiceType(models.Model):
    _name = "almonte.police.service.type"
    _description = "Tipo de servicio extraordinario"
    _order = "name"

    name = fields.Char(string="Nombre", required=True, translate=True)
    code = fields.Char(string="Código", required=True, index=True)
    unit_price = fields.Monetary(
        string="Importe del servicio",
        help="Importe total que se paga por cada servicio adjudicado, "
        "independientemente de las horas que dure.",
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
    priority_criterion_ids = fields.One2many(
        "almonte.police.priority.criterion",
        "service_type_id",
        string="Orden de prelación propio",
    )
    uses_custom_priority = fields.Boolean(
        string="Orden de prelación propio",
        compute="_compute_uses_custom_priority",
    )

    _code_unique = models.Constraint(
        "UNIQUE(code)",
        "El código del servicio debe ser único.",
    )

    @api.depends("priority_criterion_ids")
    def _compute_uses_custom_priority(self):
        for service_type in self:
            service_type.uses_custom_priority = bool(
                service_type.priority_criterion_ids
            )

    def action_customize_priority(self):
        """Copia el orden general como punto de partida del orden propio."""
        criterion_model = self.env["almonte.police.priority.criterion"]
        general = criterion_model.search([("service_type_id", "=", False)])
        for service_type in self.filtered(lambda item: not item.uses_custom_priority):
            criterion_model.create(
                [
                    {
                        "service_type_id": service_type.id,
                        "criterion_type": criterion.criterion_type,
                        "sequence": criterion.sequence,
                        "enabled": criterion.enabled,
                    }
                    for criterion in general
                ]
            )
        return True

    def action_reset_priority(self):
        self.priority_criterion_ids.unlink()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Prelación"),
                "message": _("Se aplicará el orden de prelación general."),
                "type": "info",
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }
