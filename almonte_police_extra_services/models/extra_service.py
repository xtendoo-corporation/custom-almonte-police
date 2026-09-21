# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class PoliceExtraService(models.Model):
    _name = "almonte.police.extra.service"
    _description = "Oferta de servicio extraordinario policial"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "service_date desc, id desc"

    name = fields.Char(
        string="Nº oferta",
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _("Nuevo"),
        tracking=True,
    )
    service_date = fields.Date(
        string="Fecha del servicio",
        required=True,
        tracking=True,
    )
    required_shift_id = fields.Many2one(
        "almonte.police.shift.type",
        string="Turno requerido",
        required=True,
        ondelete="restrict",
    )
    service_type_id = fields.Many2one(
        "almonte.police.service.type",
        string="Tipo de servicio",
        required=True,
        ondelete="restrict",
    )
    police_category = fields.Selection(
        selection=[
            ("inspector", "Inspectora"),
            ("subinspector", "Subinspector"),
            ("officer", "Oficial Policía"),
            ("organizational_officer", "Oficial Policía Organizativo"),
            ("police", "Policía"),
        ],
        string="Puesto / categoría",
    )
    vacancies = fields.Integer(string="Plazas", required=True, default=1)
    hours = fields.Float(string="Horas", default=0.0)
    department = fields.Char(string="Motivo / departamento")
    notes = fields.Text(string="Observaciones")
    state = fields.Selection(
        selection=[
            ("draft", "Borrador"),
            ("published", "Publicada"),
            ("assigned", "Adjudicada"),
            ("closed", "Cerrada"),
            ("cancelled", "Cancelada"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    signup_ids = fields.One2many(
        "almonte.police.extra.service.signup",
        "service_id",
        string="Inscritos",
    )
    history_ids = fields.One2many(
        "almonte.police.extra.service.history",
        "service_id",
        string="Histórico generado",
    )
    currency_id = fields.Many2one(
        related="service_type_id.currency_id",
        readonly=True,
    )
    total_amount = fields.Monetary(
        string="Importe total",
        compute="_compute_total_amount",
        currency_field="currency_id",
        store=True,
    )
    my_signup_id = fields.Many2one(
        "almonte.police.extra.service.signup",
        string="Mi inscripción",
        compute="_compute_my_signup_id",
    )
    can_volunteer = fields.Boolean(
        string="Puedo apuntarme",
        compute="_compute_my_signup_id",
    )

    @api.depends("signup_ids.employee_id", "state")
    def _compute_my_signup_id(self):
        employee = self.env.user.employee_id
        for record in self:
            signup = (
                record.signup_ids.filtered(
                    lambda item, employee=employee: item.employee_id == employee
                )
                if employee
                else self.env["almonte.police.extra.service.signup"]
            )
            record.my_signup_id = signup[:1]
            record.can_volunteer = bool(
                employee and record.state == "published" and not signup
            )

    @api.depends("signup_ids.amount", "signup_ids.state")
    def _compute_total_amount(self):
        for record in self:
            record.total_amount = sum(
                signup.amount
                for signup in record.signup_ids
                if signup.state in ("assigned_free", "assigned_double")
            )

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env.ref(
            "almonte_police_extra_services.seq_police_extra_service"
        )
        for vals in vals_list:
            if vals.get("name", _("Nuevo")) == _("Nuevo"):
                vals["name"] = sequence.next_by_id()
        return super().create(vals_list)

    @api.constrains("vacancies", "hours")
    def _check_positive_values(self):
        for record in self:
            if record.vacancies < 1:
                raise ValidationError(_("Una oferta debe tener al menos una plaza."))
            if record.hours < 0:
                raise ValidationError(_("Las horas no pueden ser negativas."))

    def action_publish(self):
        self.write({"state": "published"})

    def action_volunteer(self):
        """Autoinscripción de un agente como voluntario en un servicio publicado.

        Pensado para el grupo ``group_police_extra_agent``: el propio agente
        se apunta a un servicio ya publicado sin necesitar permisos de
        escritura sobre la oferta.
        """
        self.ensure_one()
        employee = self.env.user.employee_id
        if not employee:
            raise UserError(
                _("Tu usuario no está vinculado a ningún agente de la Policía Local.")
            )
        if self.state != "published":
            raise UserError(_("Solo puedes apuntarte a servicios publicados."))
        if self.signup_ids.filtered(lambda s: s.employee_id == employee):
            raise UserError(_("Ya estás inscrito en este servicio."))
        self.env["almonte.police.extra.service.signup"].create(
            {"service_id": self.id, "employee_id": employee.id}
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Inscripción realizada"),
                "message": _("Te has apuntado como voluntario a %s.", self.name),
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def action_assign(self):
        for service in self:
            if service.state not in ("published", "assigned"):
                raise UserError(_("Solo se pueden adjudicar ofertas publicadas."))
            candidates = service.signup_ids.filtered(
                lambda signup: signup.state in ("eligible_free", "eligible_double")
            )
            candidates.sorted(key=lambda signup: (signup.priority_score, signup.id))
            ordered = sorted(candidates, key=lambda signup: signup.priority_score)
            assigned = ordered[: service.vacancies]
            for signup in service.signup_ids:
                if signup in assigned:
                    signup.state = (
                        "assigned_free" if signup.is_free else "assigned_double"
                    )
                    self.env["almonte.police.extra.service.history"].create(
                        {
                            "service_id": service.id,
                            "employee_id": signup.employee_id.id,
                            "service_date": service.service_date,
                            "service_type_id": service.service_type_id.id,
                            "hours": service.hours,
                            "amount": signup.amount,
                            "notes": service.notes,
                        }
                    )
                    signup._send_resolution_email()
                elif signup.state in ("eligible_free", "eligible_double"):
                    signup.state = (
                        "waiting_free" if signup.is_free else "waiting_double"
                    )
                    signup._send_resolution_email()
            service.state = "assigned"


class PoliceExtraServiceSignup(models.Model):
    _name = "almonte.police.extra.service.signup"
    _description = "Inscripción en servicio extraordinario"
    _order = "priority_score, id"

    service_id = fields.Many2one(
        "almonte.police.extra.service",
        string="Servicio extraordinario",
        required=True,
        ondelete="cascade",
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Agente",
        required=True,
        ondelete="restrict",
    )
    row_number = fields.Integer(
        string="Nº de fila",
        default=0,
        readonly=True,
    )
    previous_shift_id = fields.Many2one(
        "almonte.police.shift.type",
        string="Turno anterior",
        compute="_compute_availability",
    )
    current_shift_id = fields.Many2one(
        "almonte.police.shift.type",
        string="Turno del día",
        compute="_compute_availability",
    )
    accumulated_extra_days = fields.Float(
        string="Extras acumulados",
        compute="_compute_availability",
    )
    is_free = fields.Boolean(string="Está libre", compute="_compute_availability")
    availability_status = fields.Char(
        string="Estado de disponibilidad",
        compute="_compute_availability",
    )
    priority_score = fields.Integer(
        string="Puntuación de prioridad",
        compute="_compute_availability",
        store=True,
    )
    rank = fields.Integer(
        string="Ranking",
        compute="_compute_rank",
        store=True,
    )
    state = fields.Selection(
        selection=[
            ("registered", "Inscrito"),
            ("unavailable", "No disponible"),
            ("restricted", "Restringido"),
            ("eligible_free", "Inscrito libre"),
            ("eligible_double", "Inscrito doblando"),
            ("assigned_free", "Adjudicado libre"),
            ("assigned_double", "Adjudicado doblando"),
            ("waiting_free", "En espera libre"),
            ("waiting_double", "En espera doblando"),
        ],
        compute="_compute_state",
        store=True,
        readonly=False,
    )
    amount = fields.Monetary(
        string="Importe",
        compute="_compute_amount",
        currency_field="currency_id",
        store=True,
    )
    currency_id = fields.Many2one(related="service_id.currency_id")

    _employee_service_unique = models.Constraint(
        "UNIQUE(service_id, employee_id)",
        "Un agente solo puede inscribirse una vez en la oferta.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        self_service_group = "almonte_police_extra_services.group_police_extra_agent"
        coordinator_group = "almonte_police_extra_services.group_police_extra_user"
        if self.env.user.has_group(self_service_group) and not self.env.user.has_group(
            coordinator_group
        ):
            employee = self.env.user.employee_id
            if not employee:
                raise UserError(
                    _(
                        "Tu usuario no está vinculado a ningún agente de la "
                        "Policía Local."
                    )
                )
            for vals in vals_list:
                service = (
                    self.env["almonte.police.extra.service"]
                    .browse(vals.get("service_id"))
                    .exists()
                )
                if vals.get("employee_id") != employee.id:
                    raise AccessError(
                        _("Solo puedes inscribirte tú mismo como agente voluntario.")
                    )
                if not service or service.state != "published":
                    raise UserError(
                        _("Solo puedes inscribirte en servicios publicados.")
                    )
        next_row_by_service = {}
        for vals in vals_list:
            if vals.get("row_number", 0):
                continue
            service_id = vals.get("service_id")
            if not service_id:
                continue
            if service_id not in next_row_by_service:
                last_signup = self.search(
                    [("service_id", "=", service_id)],
                    order="row_number desc, id desc",
                    limit=1,
                )
                next_row_by_service[service_id] = last_signup.row_number + 1
            vals["row_number"] = next_row_by_service[service_id]
            next_row_by_service[service_id] += 1
        return super().create(vals_list)

    def action_withdraw(self):
        """Permite a un agente retirar su propia inscripción no resuelta.

        Usa ``sudo`` de forma controlada porque el grupo autoservicio no
        tiene permiso de borrado en ir.model.access.csv (igual que el resto
        de grupos del módulo); la comprobación de propiedad y de estado se
        hace en Python antes de ejecutar el ``unlink``.
        """
        self.ensure_one()
        if not self.employee_id.user_id or self.employee_id.user_id != self.env.user:
            raise AccessError(_("Solo puedes retirar tu propia inscripción."))
        if self.state in (
            "assigned_free",
            "assigned_double",
            "waiting_free",
            "waiting_double",
        ):
            raise UserError(_("La inscripción ya está resuelta y no se puede retirar."))
        self.sudo().unlink()
        return {"type": "ir.actions.client", "tag": "reload"}

    def _send_resolution_email(self):
        """Notifica por email al agente cuando su inscripción se resuelve.

        Se dispara desde ``PoliceExtraService.action_assign`` tanto si el
        agente resulta adjudicado como si queda en lista de espera.
        """
        assigned_template = self.env.ref(
            "almonte_police_extra_services.mail_template_signup_assigned",
            raise_if_not_found=False,
        )
        waiting_template = self.env.ref(
            "almonte_police_extra_services.mail_template_signup_waiting",
            raise_if_not_found=False,
        )
        for signup in self:
            recipient = (
                signup.employee_id.user_id.email or signup.employee_id.work_email
            )
            if not recipient:
                continue
            if signup.state in ("assigned_free", "assigned_double"):
                template = assigned_template
            elif signup.state in ("waiting_free", "waiting_double"):
                template = waiting_template
            else:
                template = False
            if template:
                template.sudo().send_mail(signup.id, force_send=False)

    def action_open_employee_schedule(self):
        self.ensure_one()
        calendar_view = self.env.ref(
            "almonte_police_extra_services.view_schedule_assignment_calendar"
        )
        list_view = self.env.ref(
            "almonte_police_extra_services.view_schedule_assignment_list"
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Cuadrante de %s", self.employee_id.name),
            "res_model": "almonte.police.schedule.assignment",
            "view_mode": "calendar,list",
            "views": [
                (calendar_view.id, "calendar"),
                (list_view.id, "list"),
            ],
            "domain": [("employee_id", "=", self.employee_id.id)],
            "context": {"default_employee_id": self.employee_id.id},
            "target": "new",
        }

    def _historical_extra_days(self):
        self.ensure_one()
        service_date = self.service_id.service_date
        quarter_start_month = ((service_date.month - 1) // 3) * 3 + 1
        quarter_start = service_date.replace(
            month=quarter_start_month,
            day=1,
        )
        if quarter_start_month == 10:
            next_quarter = service_date.replace(
                year=service_date.year + 1,
                month=1,
                day=1,
            )
        else:
            next_quarter = service_date.replace(
                month=quarter_start_month + 3,
                day=1,
            )
        return sum(
            self.env["almonte.police.extra.service.history"]
            .search(
                [
                    ("employee_id", "=", self.employee_id.id),
                    ("service_date", ">=", quarter_start),
                    ("service_date", "<", next_quarter),
                ]
            )
            .mapped("days")
        )

    def _is_restricted(self, current_shift, previous_shift):
        service = self.service_id
        if not current_shift:
            return True, "Sin cuadrante"
        if current_shift.state_kind == "absence":
            return True, current_shift.code
        restriction = self.env["almonte.police.shift.restriction"].search(
            [
                ("previous_shift_id", "=", previous_shift.id if previous_shift else 0),
                ("required_shift_id", "=", service.required_shift_id.id),
                ("active", "=", True),
            ],
            limit=1,
        )
        return bool(restriction), restriction.reason if restriction else ""

    @api.depends(
        "employee_id",
        "service_id.service_date",
        "service_id.required_shift_id",
        "service_id.signup_ids.row_number",
    )
    def _compute_availability(self):
        assignment_model = self.env["almonte.police.schedule.assignment"]
        for signup in self:
            current = assignment_model.get_for_employee_date(
                signup.employee_id, signup.service_id.service_date
            ).shift_type_id
            previous = assignment_model.get_for_employee_date(
                signup.employee_id,
                signup.service_id.service_date - timedelta(days=1),
            ).shift_type_id
            restricted, reason = signup._is_restricted(current, previous)
            signup.current_shift_id = current
            signup.previous_shift_id = previous
            signup.accumulated_extra_days = signup._historical_extra_days()
            signup.is_free = bool(current and current.state_kind == "rest")
            signup.availability_status = (
                f"NO DISPONIBLE ({current.code})"
                if restricted and current.state_kind == "absence"
                else "RESTRINGIDO (Noche a Mañana)"
                if restricted
                else "DISPONIBLE LIBRE"
                if signup.is_free
                else "DISPONIBLE DOBLANDO"
            )
            if restricted:
                signup.priority_score = 99999
            elif signup.is_free:
                signup.priority_score = (
                    int(signup.accumulated_extra_days * 1000) + signup.row_number
                )
            else:
                signup.priority_score = (
                    50000
                    + int(signup.accumulated_extra_days * 1000)
                    + signup.row_number
                )

    @api.depends("priority_score", "service_id.signup_ids.priority_score")
    def _compute_rank(self):
        for signup in self:
            scores = [
                item.priority_score
                for item in signup.service_id.signup_ids
                if item.priority_score < 99999
            ]
            signup.rank = (
                1 + sum(score < signup.priority_score for score in scores)
                if signup.priority_score < 99999
                else 0
            )

    @api.depends("availability_status", "is_free", "priority_score")
    def _compute_state(self):
        for signup in self:
            if signup.state in (
                "assigned_free",
                "assigned_double",
                "waiting_free",
                "waiting_double",
            ):
                continue
            if signup.priority_score == 99999:
                signup.state = (
                    "restricted"
                    if signup.availability_status.startswith("RESTRINGIDO")
                    else "unavailable"
                )
            elif signup.is_free:
                signup.state = "eligible_free"
            else:
                signup.state = "eligible_double"

    @api.depends("service_id.service_type_id", "service_id.hours")
    def _compute_amount(self):
        for signup in self:
            service_type = signup.service_id.service_type_id
            signup.amount = (
                service_type.unit_price
                if service_type.pricing_mode == "fixed"
                else service_type.unit_price * signup.service_id.hours
            )


class PoliceExtraServiceHistory(models.Model):
    _name = "almonte.police.extra.service.history"
    _description = "Histórico de servicios extraordinarios realizados"
    _order = "service_date desc, id desc"

    service_id = fields.Many2one(
        "almonte.police.extra.service",
        required=True,
        ondelete="cascade",
    )
    employee_id = fields.Many2one("hr.employee", required=True, ondelete="restrict")
    service_date = fields.Date(string="Fecha del servicio", required=True, index=True)
    service_type_id = fields.Many2one(
        "almonte.police.service.type",
        string="Tipo de servicio",
        required=True,
        ondelete="restrict",
    )
    hours = fields.Float(string="Horas")
    days = fields.Float(string="Días", default=1.0, required=True)
    amount = fields.Monetary(
        string="Importe",
        currency_field="currency_id",
        required=True,
    )
    currency_id = fields.Many2one(
        related="service_type_id.currency_id",
        readonly=True,
    )
    notes = fields.Text(string="Observaciones")

    _employee_service_unique = models.Constraint(
        "UNIQUE(service_id, employee_id)",
        "El histórico del servicio para este agente ya existe.",
    )
