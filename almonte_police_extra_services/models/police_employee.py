# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    police_category = fields.Selection(
        selection=[
            ("inspector", "Inspectora"),
            ("subinspector", "Subinspector"),
            ("officer", "Oficial Policía"),
            ("organizational_officer", "Oficial Policía Organizativo"),
            ("police", "Policía"),
        ],
        string="Categoría policial",
        tracking=True,
    )
    police_dip = fields.Char(string="DIP", index=True, tracking=True)
    police_group = fields.Char(string="Grupo policial", index=True, tracking=True)
    police_employee_id = fields.Integer(
        string="ID empleado Policía",
        index=True,
        copy=False,
        help="Identificador usado en el Excel histórico del Ayuntamiento.",
    )
    police_schedule_assignment_ids = fields.One2many(
        "almonte.police.schedule.assignment",
        "employee_id",
        string="Cuadrante",
    )

    _police_dip_unique = models.Constraint(
        "UNIQUE(police_dip)",
        "El DIP debe ser único.",
    )
    _police_employee_id_unique = models.Constraint(
        "UNIQUE(police_employee_id)",
        "El ID empleado Policía debe ser único.",
    )

    def action_create_police_user(self):
        """Crea el usuario Odoo de un agente y le envía la invitación segura.

        Restringido a ``group_police_extra_manager`` (responsables). No se
        maneja ni se expone ninguna contraseña: el propio agente la
        establece a través del enlace de invitación que genera
        ``action_reset_password`` (módulo estándar ``auth_signup``).
        """
        self.ensure_one()
        if not self.env.user.has_group(
            "almonte_police_extra_services.group_police_extra_manager"
        ):
            raise AccessError(
                _(
                    "Solo un responsable de Policía Local puede crear "
                    "usuarios Odoo para los agentes."
                )
            )
        if self.user_id:
            raise UserError(_("Este agente ya tiene un usuario Odoo vinculado."))
        if not self.police_category:
            raise UserError(
                _("Informa la categoría policial antes de crear el usuario.")
            )
        if not self.work_email:
            raise UserError(
                _(
                    "Informa el email de trabajo del agente antes de crear "
                    "su usuario Odoo."
                )
            )
        agent_group = self.env.ref(
            "almonte_police_extra_services.group_police_extra_agent"
        )
        user = (
            self.env["res.users"]
            .sudo()
            .create(
                {
                    "name": self.name,
                    "login": self.work_email,
                    "email": self.work_email,
                    "create_employee_id": self.id,
                    "group_ids": [(6, 0, [agent_group.id])],
                }
            )
        )
        user.sudo().with_context(create_user=True).action_reset_password()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Usuario creado"),
                "message": _(
                    "Se ha creado el usuario de %s y se le ha enviado un "
                    "correo para establecer su contraseña.",
                    self.name,
                ),
                "type": "success",
            },
        }
