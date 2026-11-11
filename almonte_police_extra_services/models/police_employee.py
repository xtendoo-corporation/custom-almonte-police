# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

import secrets
import string

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError

PASSWORD_ALPHABET = "".join(
    char for char in string.ascii_letters + string.digits if char not in "0OoIl1"
)
PASSWORD_LENGTH = 10


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    police_category_id = fields.Many2one(
        "almonte.police.category",
        string="Categoría policial",
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    police_dip = fields.Char(string="DIP", index=True, tracking=True)
    police_group_id = fields.Many2one(
        "almonte.police.group",
        string="Grupo policial",
        ondelete="restrict",
        index=True,
        tracking=True,
    )
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

    def _check_police_user_manager(self):
        if not self.env.user.has_group(
            "almonte_police_extra_services.group_police_extra_manager"
        ):
            raise AccessError(
                _(
                    "Solo un responsable de Policía Local puede gestionar los "
                    "usuarios de los agentes."
                )
            )

    @staticmethod
    def _generate_police_password():
        return "".join(
            secrets.choice(PASSWORD_ALPHABET) for _index in range(PASSWORD_LENGTH)
        )

    def _set_police_password(self):
        """Fija una clave temporal en el usuario (login = DIP) y la devuelve."""
        self.ensure_one()
        password = self._generate_police_password()
        self.user_id.sudo().write({"login": self.police_dip, "password": password})
        return password

    def _password_notification(self, title, password):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": _(
                    "Usuario: %(login)s · Contraseña temporal: %(password)s. "
                    "Entrégasela al agente; no se volverá a mostrar.",
                    login=self.police_dip,
                    password=password,
                ),
                "type": "warning",
                "sticky": True,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_create_police_user(self):
        """Crea el usuario Odoo de un agente usando su DIP como login.

        Restringido a ``group_police_extra_manager`` (responsables). Se genera
        una contraseña temporal aleatoria que se muestra una sola vez al
        responsable para que se la entregue al agente; no se guarda en claro.
        """
        self.ensure_one()
        self._check_police_user_manager()
        if self.user_id:
            raise UserError(_("Este agente ya tiene un usuario Odoo vinculado."))
        if not self.police_category_id:
            raise UserError(
                _("Informa la categoría policial antes de crear el usuario.")
            )
        if not self.police_dip:
            raise UserError(_("Informa el DIP del agente antes de crear su usuario."))
        self._check_police_login_available()
        agent_group = self.env.ref(
            "almonte_police_extra_services.group_police_extra_agent"
        )
        self.env["res.users"].sudo().create(
            {
                "name": self.name,
                "login": self.police_dip,
                "email": self.work_email,
                "create_employee_id": self.id,
                "group_ids": [(6, 0, [agent_group.id])],
            }
        )
        return self._password_notification(
            _("Usuario creado"), self._set_police_password()
        )

    def action_regenerate_police_password(self):
        """Genera una nueva contraseña temporal y fija el DIP como login."""
        self.ensure_one()
        self._check_police_user_manager()
        if not self.user_id:
            raise UserError(_("Este agente todavía no tiene usuario Odoo."))
        if not self.police_dip:
            raise UserError(_("Informa el DIP del agente antes de regenerar la clave."))
        self._check_police_login_available()
        return self._password_notification(
            _("Contraseña regenerada"), self._set_police_password()
        )

    def _check_police_login_available(self):
        self.ensure_one()
        duplicated = (
            self.env["res.users"]
            .sudo()
            .with_context(active_test=False)
            .search(
                [("login", "=", self.police_dip), ("id", "!=", self.user_id.id)],
                limit=1,
            )
        )
        if duplicated:
            raise UserError(
                _(
                    "Ya existe otro usuario con el login %(login)s (%(user)s).",
                    login=self.police_dip,
                    user=duplicated.name,
                )
            )
