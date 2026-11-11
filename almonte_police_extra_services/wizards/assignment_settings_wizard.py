# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, fields, models


class PoliceAssignmentSettingsWizard(models.TransientModel):
    _name = "almonte.police.assignment.settings.wizard"
    _description = "Parámetros de adjudicación de servicios extraordinarios"

    min_rest_hours = fields.Float(
        string="Descanso mínimo antes de un extra (horas)",
        required=True,
        default=lambda self: self.env.company.police_min_rest_hours,
    )

    def action_apply(self):
        self.ensure_one()
        self.env.company.sudo().police_min_rest_hours = self.min_rest_hours
        pending = self.env["almonte.police.extra.service.signup"].search(
            [("service_id.state", "=", "published")]
        )
        pending._compute_availability()
        pending._compute_state()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Parámetros de adjudicación"),
                "message": _(
                    "Descanso mínimo fijado en %s horas.",
                    f"{self.min_rest_hours:g}",
                ),
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
