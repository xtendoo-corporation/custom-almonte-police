# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Recalcula la puntuación de las ofertas abiertas con los criterios."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["almonte.police.priority.criterion"]._recompute_pending_signups()
