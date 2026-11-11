# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    police_baremo_reset_date = fields.Date(
        string="Baremo a cero desde",
        help="Los servicios extraordinarios anteriores a esta fecha no "
        "computan para el orden de adjudicación. El histórico no se borra.",
    )
