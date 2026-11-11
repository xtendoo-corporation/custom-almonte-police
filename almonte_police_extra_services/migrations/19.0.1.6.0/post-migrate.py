# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import SUPERUSER_ID, api

SHIFT_HOURS = {
    "almonte_police_extra_services.shift_morning": (7.0, 15.0),
    "almonte_police_extra_services.shift_afternoon": (15.0, 23.0),
    "almonte_police_extra_services.shift_night": (23.0, 7.0),
}


def migrate(cr, version):
    """Configura el horario de los turnos M/T/N y recalcula la prelación."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid, (start_hour, end_hour) in SHIFT_HOURS.items():
        shift = env.ref(xmlid, raise_if_not_found=False)
        if shift and shift.start_hour == shift.end_hour:
            shift.write({"start_hour": start_hour, "end_hour": end_hour})
    signups = env["almonte.police.extra.service.signup"].search([])
    signups._compute_availability()
    signups._compute_state()
