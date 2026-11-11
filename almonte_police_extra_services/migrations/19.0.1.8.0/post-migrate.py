# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import SUPERUSER_ID, api

REQUESTABLE_SHIFTS = (
    "almonte_police_extra_services.shift_vacation",
    "almonte_police_extra_services.shift_personal_leave",
)
UNION_HOURS_SHIFT = "almonte_police_extra_services.shift_union_hours"
REPORTS_MENU = "almonte_police_extra_services.menu_reports"


def migrate(cr, version):
    """Aplica a los tipos de turno existentes (noupdate) los nuevos valores."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid in REQUESTABLE_SHIFTS:
        shift = env.ref(xmlid, raise_if_not_found=False)
        if shift:
            shift.agent_requestable = True
    union_hours = env.ref(UNION_HOURS_SHIFT, raise_if_not_found=False)
    if union_hours and not union_hours.alias_codes:
        code_map = env["almonte.police.shift.type"]._get_code_map()
        if "HRS" not in code_map:
            union_hours.alias_codes = "HRS"
    reports_menu = env.ref(REPORTS_MENU, raise_if_not_found=False)
    if reports_menu and reports_menu.action:
        reports_menu.action = False
