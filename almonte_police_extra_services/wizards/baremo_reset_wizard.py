# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, fields, models


class PoliceBaremoResetWizard(models.TransientModel):
    _name = "almonte.police.baremo.reset.wizard"
    _description = "Poner a cero el baremo de servicios extraordinarios"

    current_reset_date = fields.Date(
        string="Puesta a cero vigente",
        default=lambda self: self.env.company.police_baremo_reset_date,
        readonly=True,
    )
    reset_date = fields.Date(
        string="Poner a cero desde",
        required=True,
        default=fields.Date.context_today,
    )

    def _notify(self, message):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Baremo de servicios extraordinarios"),
                "message": message,
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_apply(self):
        self.ensure_one()
        self.env.company.sudo().police_baremo_reset_date = self.reset_date
        return self._notify(
            _(
                "El baremo computa desde el %s. El histórico se conserva.",
                self.reset_date.strftime("%d/%m/%Y"),
            )
        )

    def action_clear(self):
        self.ensure_one()
        self.env.company.sudo().police_baremo_reset_date = False
        return self._notify(_("El baremo vuelve a computar el trimestre completo."))
