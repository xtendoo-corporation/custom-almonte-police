# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models


class PoliceCategory(models.Model):
    _name = "almonte.police.category"
    _description = "Categoría de agente de la Policía Local"
    _order = "sequence, name"

    name = fields.Char(string="Nombre", required=True)
    sequence = fields.Integer(string="Secuencia", default=10)
    active = fields.Boolean(string="Activo", default=True)
    employee_ids = fields.One2many(
        "hr.employee",
        "police_category_id",
        string="Agentes",
    )
    employee_count = fields.Integer(
        string="Nº agentes",
        compute="_compute_employee_count",
    )

    _name_unique = models.Constraint(
        "UNIQUE(name)",
        "Ya existe una categoría de agente con ese nombre.",
    )

    @api.depends("employee_ids")
    def _compute_employee_count(self):
        for record in self:
            record.employee_count = len(record.employee_ids)


class PoliceGroup(models.Model):
    _name = "almonte.police.group"
    _description = "Grupo de la Policía Local"
    _order = "sequence, name"

    name = fields.Char(string="Nombre", required=True)
    sequence = fields.Integer(string="Secuencia", default=10)
    active = fields.Boolean(string="Activo", default=True)
    employee_ids = fields.One2many(
        "hr.employee",
        "police_group_id",
        string="Agentes",
    )
    employee_count = fields.Integer(
        string="Nº agentes",
        compute="_compute_employee_count",
    )

    _name_unique = models.Constraint(
        "UNIQUE(name)",
        "Ya existe un grupo policial con ese nombre.",
    )

    @api.depends("employee_ids")
    def _compute_employee_count(self):
        for record in self:
            record.employee_count = len(record.employee_ids)
