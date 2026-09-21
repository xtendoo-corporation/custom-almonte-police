# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models


class PoliceExtraSummary(models.Model):
    _name = "almonte.police.extra.summary"
    _description = "Resumen económico de extras por policía"
    _auto = False
    _rec_name = "employee_id"

    employee_id = fields.Many2one("hr.employee", string="Policía", readonly=True)
    service_count = fields.Integer(string="Nº servicios", readonly=True)
    total_hours = fields.Float(string="Horas realizadas", readonly=True)
    total_amount = fields.Monetary(
        string="Total acumulado",
        currency_field="currency_id",
        readonly=True,
    )
    currency_id = fields.Many2one("res.currency", readonly=True)

    def init(self):
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW almonte_police_extra_summary AS (
                SELECT
                    MIN(h.id) AS id,
                    h.employee_id AS employee_id,
                    COUNT(h.id) AS service_count,
                    COALESCE(SUM(h.hours), 0) AS total_hours,
                    COALESCE(SUM(h.amount), 0) AS total_amount,
                    st.currency_id AS currency_id
                FROM almonte_police_extra_service_history h
                JOIN almonte_police_service_type st
                    ON st.id = h.service_type_id
                GROUP BY h.employee_id, st.currency_id
            )
        """)
