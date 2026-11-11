# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import SUPERUSER_ID, Command, api

MODULE = "almonte_police_extra_services"
AGENT_GROUPS = ("group_police_extra_agent",)
MANAGEMENT_GROUPS = ("group_police_extra_user", "group_police_extra_manager")
MENU_GROUPS = {
    "menu_services": AGENT_GROUPS,
    "menu_management": MANAGEMENT_GROUPS,
    "menu_extra_services": MANAGEMENT_GROUPS,
    "menu_schedule": MANAGEMENT_GROUPS,
    "menu_schedule_generate": MANAGEMENT_GROUPS,
    "menu_schedule_import": MANAGEMENT_GROUPS,
    "menu_absence_requests": MANAGEMENT_GROUPS,
}
REMOVED_RECORDS = ("menu_working_schedules", "action_working_schedules")


def migrate(cr, version):
    """Fija los grupos exactos de los menús separados en dos ramas.

    ``<menuitem groups>`` solo añade grupos al actualizar, así que los menús
    de gestión conservarían el grupo de agente de versiones anteriores.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    for menu_xmlid, group_xmlids in MENU_GROUPS.items():
        menu = env.ref(f"{MODULE}.{menu_xmlid}", raise_if_not_found=False)
        if not menu:
            continue
        groups = [env.ref(f"{MODULE}.{xmlid}").id for xmlid in group_xmlids]
        menu.group_ids = [Command.set(groups)]
    reports_menu = env.ref(f"{MODULE}.menu_reports", raise_if_not_found=False)
    if reports_menu and reports_menu.action:
        reports_menu.action = False
    for xmlid in REMOVED_RECORDS:
        record = env.ref(f"{MODULE}.{xmlid}", raise_if_not_found=False)
        if record:
            record.unlink()
