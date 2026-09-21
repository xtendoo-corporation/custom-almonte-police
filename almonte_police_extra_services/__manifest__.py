# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)
{
    "name": "Almonte - Policía Local y Servicios Extraordinarios",
    "summary": (
        "Cuadrantes, disponibilidad, adjudicación equitativa y cálculo "
        "económico de servicios extraordinarios de la Policía Local."
    ),
    "version": "19.0.1.1.0",
    "category": "Human Resources",
    "author": "Xtendoo, Tecnativa",
    "website": "https://www.xtendoo.es",
    "license": "LGPL-3",
    "depends": ["base", "hr", "mail", "auth_signup"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/shift_types.xml",
        "data/sequences.xml",
        "data/police_employees.xml",
        "data/mail_template_data.xml",
        "views/police_employee_views.xml",
        "views/shift_views.xml",
        "views/service_views.xml",
        "views/report_views.xml",
        "views/menuitems.xml",
    ],
    "demo": ["demo/demo.xml"],
    "installable": True,
    "application": True,
    "auto_install": False,
}
