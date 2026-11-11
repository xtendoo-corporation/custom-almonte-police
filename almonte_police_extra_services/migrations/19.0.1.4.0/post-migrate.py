# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
"""Migra las categorías y grupos policiales a modelos mantenibles.

* ``hr.employee``: ``police_category`` (selection) y ``police_group`` (char)
  pasan a ``police_category_id`` y ``police_group_id``.
* ``almonte.police.extra.service``: ``police_category`` (selection) o el
  intermedio ``police_category_id`` pasan a ``police_category_ids``.
* El importe de las inscripciones pasa a ser siempre la tarifa total.
"""

from odoo import SUPERUSER_ID, api
from odoo.tools.sql import column_exists

MODULE = "almonte_police_extra_services"


def _get_category(env, key):
    category = env.ref(f"{MODULE}.police_category_{key}", False)
    if category:
        return category
    category_model = env["almonte.police.category"].with_context(active_test=False)
    return category_model.search([("name", "=", key)], limit=1) or (
        category_model.create({"name": key})
    )


def _migrate_employee_categories(env):
    if not column_exists(env.cr, "hr_employee", "police_category"):
        return
    env.cr.execute(
        "SELECT DISTINCT police_category FROM hr_employee "
        "WHERE police_category IS NOT NULL"
    )
    for (key,) in env.cr.fetchall():
        env.cr.execute(
            "UPDATE hr_employee SET police_category_id = %s "
            "WHERE police_category = %s AND police_category_id IS NULL",
            (_get_category(env, key).id, key),
        )
    env.cr.execute("ALTER TABLE hr_employee DROP COLUMN police_category")


def _migrate_employee_groups(env):
    if not column_exists(env.cr, "hr_employee", "police_group"):
        return
    group_model = env["almonte.police.group"].with_context(active_test=False)
    env.cr.execute(
        "SELECT DISTINCT TRIM(police_group) FROM hr_employee "
        "WHERE COALESCE(TRIM(police_group), '') != ''"
    )
    for (name,) in env.cr.fetchall():
        group = group_model.search([("name", "=", name)], limit=1)
        group = group or group_model.create({"name": name})
        env.cr.execute(
            "UPDATE hr_employee SET police_group_id = %s "
            "WHERE TRIM(police_group) = %s AND police_group_id IS NULL",
            (group.id, name),
        )
    env.cr.execute("ALTER TABLE hr_employee DROP COLUMN police_group")


def _migrate_service_categories(env):
    table = "almonte_police_extra_service"
    insert_query = (
        "INSERT INTO almonte_police_extra_service_category_rel "
        "(service_id, category_id) SELECT id, %s FROM almonte_police_extra_service "
        "WHERE {column} = %s ON CONFLICT DO NOTHING"
    )
    if column_exists(env.cr, table, "police_category"):
        env.cr.execute(
            "SELECT DISTINCT police_category FROM almonte_police_extra_service "
            "WHERE police_category IS NOT NULL"
        )
        for (key,) in env.cr.fetchall():
            env.cr.execute(
                insert_query.format(column="police_category"),
                (_get_category(env, key).id, key),
            )
        env.cr.execute(f"ALTER TABLE {table} DROP COLUMN police_category")
    if column_exists(env.cr, table, "police_category_id"):
        env.cr.execute(
            "INSERT INTO almonte_police_extra_service_category_rel "
            "(service_id, category_id) SELECT id, police_category_id "
            "FROM almonte_police_extra_service "
            "WHERE police_category_id IS NOT NULL ON CONFLICT DO NOTHING"
        )
        env.cr.execute(f"ALTER TABLE {table} DROP COLUMN police_category_id")


def _migrate_amounts(env):
    if column_exists(env.cr, "almonte_police_service_type", "pricing_mode"):
        env.cr.execute(
            "ALTER TABLE almonte_police_service_type DROP COLUMN pricing_mode"
        )
    env.cr.execute(
        """
        UPDATE almonte_police_extra_service_signup signup
           SET amount = service_type.unit_price
          FROM almonte_police_extra_service service
          JOIN almonte_police_service_type service_type
            ON service_type.id = service.service_type_id
         WHERE service.id = signup.service_id
           AND signup.amount IS DISTINCT FROM service_type.unit_price
        """
    )


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _migrate_employee_categories(env)
    _migrate_employee_groups(env)
    _migrate_service_categories(env)
    _migrate_amounts(env)
    env.invalidate_all()
