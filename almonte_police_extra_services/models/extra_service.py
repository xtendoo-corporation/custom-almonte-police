# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .priority_criterion import RANKING_WIDTHS

EXCLUDED_PRIORITY = 10**15
PRIORITY_TIERS = [
    ("1_free", "1. Libre"),
    ("2_rested", "2. Descanso mínimo antes"),
    ("3_double", "3. Dobla turno"),
    ("4_restricted", "4. Restringido"),
    ("5_unavailable", "5. No disponible"),
]
ELIGIBLE_TIERS = ("1_free", "2_rested", "3_double")
RESOLVED_SIGNUP_STATES = (
    "assigned_free",
    "assigned_double",
    "waiting_free",
    "waiting_double",
)


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
    police_category_ids = fields.Many2many(
        "almonte.police.category",
        "almonte_police_extra_service_category_rel",
        "service_id",
        "category_id",
        string="Categorías permitidas",
        help="Solo los agentes de estas categorías pueden inscribirse. "
        "Si se deja vacío, pueden inscribirse agentes de todas las categorías.",
    )
    signup_deadline = fields.Datetime(
        string="Límite de inscripción",
        tracking=True,
        help="Fecha y hora a partir de la cual los agentes ya no pueden "
        "apuntarse ni retirarse. Si se deja vacío, el plazo sigue abierto "
        "mientras la oferta esté publicada.",
    )
    is_signup_open = fields.Boolean(
        string="Plazo abierto",
        compute="_compute_is_signup_open",
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

    @api.depends("state", "signup_deadline")
    def _compute_is_signup_open(self):
        now = fields.Datetime.now()
        for record in self:
            record.is_signup_open = record.state == "published" and (
                not record.signup_deadline or record.signup_deadline >= now
            )

    @api.depends(
        "signup_ids.employee_id",
        "state",
        "signup_deadline",
        "police_category_ids",
    )
    @api.depends_context("uid")
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
                employee
                and not signup
                and not record._get_signup_block_reason(employee)
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

    def _is_category_allowed(self, employee):
        self.ensure_one()
        return (
            not self.police_category_ids
            or employee.police_category_id in self.police_category_ids
        )

    def _get_signup_block_reason(self, employee):
        """Motivo por el que ``employee`` no puede apuntarse por sí mismo."""
        self.ensure_one()
        if self.state != "published":
            return _("Solo puedes apuntarte a servicios publicados.")
        if not self.is_signup_open:
            return _("El plazo de inscripción de este servicio ha finalizado.")
        if not self._is_category_allowed(employee):
            return _("Tu categoría policial no puede inscribirse en este servicio.")
        conflict = self._get_conflicting_history(employee)
        if conflict:
            return _(
                "Ya tienes adjudicado el servicio %s en la misma franja horaria.",
                conflict.service_id.name,
            )
        return False

    def _get_conflicting_history(self, employee):
        """Servicio extra ya adjudicado al agente que solapa con esta oferta.

        Se considera solape tener otro servicio el mismo día y turno, o
        cualquier servicio de los días contiguos cuyo horario se cruce con
        el de esta oferta (p. ej. una Noche del día anterior).
        """
        self.ensure_one()
        histories = (
            self.env["almonte.police.extra.service.history"]
            .sudo()
            .search(
                [
                    ("employee_id", "=", employee.id),
                    ("origin", "=", "service"),
                    ("service_id", "!=", self._origin.id or False),
                    ("service_date", ">=", self.service_date - timedelta(days=1)),
                    ("service_date", "<=", self.service_date + timedelta(days=1)),
                ]
            )
        )
        service_shift = self.required_shift_id
        service_interval = service_shift._get_interval()
        for history in histories:
            other_shift = history.service_id.required_shift_id
            day_offset = (history.service_date - self.service_date).days
            if not day_offset and other_shift == service_shift:
                return history
            other_interval = other_shift._get_interval(day_offset=day_offset)
            if (
                service_interval
                and other_interval
                and other_interval[0] < service_interval[1]
                and service_interval[0] < other_interval[1]
            ):
                return history
        return histories.browse()

    @api.constrains("signup_deadline", "service_date")
    def _check_signup_deadline(self):
        for record in self:
            if not record.signup_deadline:
                continue
            deadline_date = fields.Datetime.context_timestamp(
                record, record.signup_deadline
            ).date()
            if deadline_date > record.service_date:
                raise ValidationError(
                    _(
                        "El límite de inscripción no puede ser posterior a la "
                        "fecha del servicio."
                    )
                )

    @api.constrains("vacancies", "hours")
    def _check_positive_values(self):
        for record in self:
            if record.vacancies < 1:
                raise ValidationError(_("Una oferta debe tener al menos una plaza."))
            if record.vacancies < len(record._get_assigned_signups()):
                raise ValidationError(
                    _(
                        "No puede haber menos plazas que agentes ya adjudicados "
                        "en la oferta %s.",
                        record.name,
                    )
                )
            if record.hours < 0:
                raise ValidationError(_("Las horas no pueden ser negativas."))

    def action_publish(self):
        self.write({"state": "published"})

    def _get_assigned_signups(self):
        self.ensure_one()
        return self.signup_ids.filtered(
            lambda signup: signup.state in ("assigned_free", "assigned_double")
        )

    def action_reopen(self):
        """Reabre una oferta adjudicada para ampliar plazas.

        Las adjudicaciones ya hechas se mantienen. Los agentes que quedaron en
        lista de espera vuelven a competir por las plazas nuevas junto con los
        que se inscriban durante la reapertura.
        """
        for service in self:
            if service.state != "assigned":
                raise UserError(_("Solo se pueden reabrir ofertas adjudicadas."))
            waiting = service.signup_ids.filtered(
                lambda signup: signup.state in ("waiting_free", "waiting_double")
            )
            waiting.write({"state": "registered"})
            waiting._compute_state()
            service.state = "published"
            body = _(
                "Oferta reabierta para ampliar plazas. Se mantienen %s adjudicaciones.",
                len(service._get_assigned_signups()),
            )
            if not service.is_signup_open:
                service.signup_deadline = False
                body += " " + _(
                    "El límite de inscripción había vencido y se ha quitado; "
                    "fija uno nuevo si es necesario."
                )
            service.message_post(body=body)

    def action_print_decree(self):
        return self.env.ref(
            "almonte_police_extra_services.action_report_extra_service_decree"
        ).report_action(self)

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
        if self.signup_ids.filtered(lambda s: s.employee_id == employee):
            raise UserError(_("Ya estás inscrito en este servicio."))
        block_reason = self._get_signup_block_reason(employee)
        if block_reason:
            raise UserError(block_reason)
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
            pending = service.signup_ids.filtered(
                lambda signup: signup.state not in RESOLVED_SIGNUP_STATES
            )
            pending._compute_availability()
            pending._compute_state()
            candidates = service.signup_ids.filtered(
                lambda signup: signup.state in ("eligible_free", "eligible_double")
            )
            free_vacancies = max(
                service.vacancies - len(service._get_assigned_signups()), 0
            )
            assigned = candidates.sorted(
                key=lambda signup: (signup.priority_score, signup.id)
            )[:free_vacancies]
            for signup in service.signup_ids:
                if signup in assigned:
                    signup.state = (
                        "assigned_free" if signup.is_free else "assigned_double"
                    )
                    self.env["almonte.police.extra.service.history"].create(
                        {
                            "origin": "service",
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
    quarter_service_count = fields.Float(
        string="Servicios en el trimestre",
        compute="_compute_availability",
        digits=(16, 1),
        help="Servicios extraordinarios realizados por el agente en el "
        "trimestre de la oferta (desde la fecha de puesta a cero del baremo, "
        "si es posterior), sumando los días de extras atrasados. En igualdad "
        "de condiciones tiene prioridad quien menos servicios haya realizado.",
    )
    is_free = fields.Boolean(string="Está libre", compute="_compute_availability")
    availability_status = fields.Char(
        string="Estado de disponibilidad",
        compute="_compute_availability",
    )
    priority_tier = fields.Selection(
        selection=PRIORITY_TIERS,
        string="Prelación",
        compute="_compute_availability",
        store=True,
    )
    priority_score = fields.Float(
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
                if not service:
                    raise UserError(
                        _("Solo puedes inscribirte en servicios publicados.")
                    )
                block_reason = service._get_signup_block_reason(employee)
                if block_reason:
                    raise UserError(block_reason)
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

    @api.constrains("service_id", "employee_id")
    def _check_employee_category_allowed(self):
        for signup in self:
            if not signup.service_id._is_category_allowed(signup.employee_id):
                raise ValidationError(
                    _(
                        "La categoría policial de %(employee)s no está "
                        "permitida en el servicio %(service)s.",
                        employee=signup.employee_id.name,
                        service=signup.service_id.name,
                    )
                )

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
        if self.state in RESOLVED_SIGNUP_STATES:
            raise UserError(_("La inscripción ya está resuelta y no se puede retirar."))
        if not self.service_id.is_signup_open:
            raise UserError(
                _(
                    "El plazo de inscripción ha finalizado; ya no puedes "
                    "retirar tu inscripción."
                )
            )
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

    def _quarter_service_count(self):
        self.ensure_one()
        service_date = self.service_id.service_date
        quarter_start = service_date.replace(
            month=((service_date.month - 1) // 3) * 3 + 1,
            day=1,
        )
        if quarter_start.month == 10:
            next_quarter = quarter_start.replace(year=quarter_start.year + 1, month=1)
        else:
            next_quarter = quarter_start.replace(month=quarter_start.month + 3)
        reset_date = self.env.company.police_baremo_reset_date
        window_start = max(quarter_start, reset_date) if reset_date else quarter_start
        histories = (
            self.env["almonte.police.extra.service.history"]
            .sudo()
            .search(
                [
                    ("employee_id", "=", self.employee_id.id),
                    ("service_date", ">=", window_start),
                    ("service_date", "<", next_quarter),
                    "|",
                    ("service_id", "=", False),
                    ("service_id", "!=", self.service_id._origin.id or False),
                ]
            )
        )
        return sum(history._get_baremo_weight() for history in histories)

    def _get_shift_restriction(self, previous_shift):
        return self.env["almonte.police.shift.restriction"].search(
            [
                ("previous_shift_id", "=", previous_shift.id if previous_shift else 0),
                ("required_shift_id", "=", self.service_id.required_shift_id.id),
                ("active", "=", True),
            ],
            limit=1,
        )

    def _get_priority_tier(self, current_shift, previous_shift, active_types=None):
        """Aplica los criterios oficiales de prelación del Reglamento.

        Devuelve una tupla ``(nivel, estado legible)``:

        1. Libre ese día.
        2. Trabaja ese día pero tiene el descanso mínimo configurado en la
           compañía (8 horas por defecto) antes del servicio.
        3. Dobla turno (continuo o con menos descanso que el mínimo).
        4. Restringido: incompatibilidad configurada entre turnos (p. ej.
           Noche → Mañana) o el servicio coincide con su propio turno.
        5. No disponible: ausencias que impiden ese extra o sin cuadrante.
           Las ausencias parciales (p. ej. cita médica de mañana) que no
           impiden el turno del extra cuentan como libre.

        Cada exclusión solo se aplica si su criterio está activo en
        ``active_types`` (por defecto, todas).
        """
        self.ensure_one()
        if active_types is None:
            active_types = {
                "exclude_no_schedule",
                "exclude_absence",
                "exclude_already_assigned",
                "exclude_shift_restriction",
                "exclude_shift_overlap",
            }
        service_shift = self.service_id.required_shift_id
        if not current_shift:
            if "exclude_no_schedule" in active_types:
                return "5_unavailable", _("NO DISPONIBLE (sin cuadrante)")
            return "1_free", _("LIBRE (sin cuadrante)")
        if "exclude_absence" in active_types and current_shift._blocks_extra_shift(
            service_shift
        ):
            return "5_unavailable", _("NO DISPONIBLE (%s)", current_shift.code)
        conflict = (
            "exclude_already_assigned" in active_types
            and self.service_id._get_conflicting_history(self.employee_id)
        )
        if conflict:
            return "4_restricted", _(
                "RESTRINGIDO (ya adjudicado en %s)", conflict.service_id.name
            )
        if "exclude_shift_restriction" in active_types and (
            self._get_shift_restriction(previous_shift)
        ):
            return "4_restricted", _(
                "RESTRINGIDO (%(previous)s a %(service)s)",
                previous=previous_shift.name,
                service=service_shift.name,
            )
        if current_shift.state_kind in ("rest", "absence"):
            return "1_free", _("LIBRE")
        service_interval = service_shift._get_interval()
        work_intervals = [
            interval
            for interval in (
                current_shift._get_interval(),
                previous_shift._get_interval(day_offset=-1)
                if previous_shift and previous_shift.state_kind == "work"
                else None,
            )
            if interval
        ]
        if not service_interval or not current_shift._get_interval():
            return "3_double", _("DOBLA TURNO")
        service_start, service_end = service_interval
        if any(
            start < service_end and service_start < end for start, end in work_intervals
        ):
            if "exclude_shift_overlap" not in active_types:
                return "3_double", _("DOBLA TURNO (coincide con su turno)")
            return "4_restricted", _(
                "RESTRINGIDO (coincide con su turno de %s)", current_shift.name
            )
        if any(
            end == service_start or start == service_end
            for start, end in work_intervals
        ):
            return "3_double", _("DOBLA TURNO")
        previous_ends = [end for _start, end in work_intervals if end <= service_start]
        free_hours_before = (
            service_start - max(previous_ends) if previous_ends else float("inf")
        )
        min_rest_hours = self.env.company.police_min_rest_hours
        if free_hours_before >= min_rest_hours:
            return "2_rested", _("%s H LIBRES ANTES", f"{min_rest_hours:g}")
        return "3_double", _("DOBLA TURNO")

    @api.depends(
        "employee_id",
        "service_id.service_date",
        "service_id.required_shift_id",
        "service_id.signup_ids.row_number",
    )
    def _compute_availability(self):
        assignment_model = self.env["almonte.police.schedule.assignment"]
        criterion_model = self.env["almonte.police.priority.criterion"]
        criteria_by_type = {}
        for signup in self:
            service_type = signup.service_id.service_type_id
            if service_type not in criteria_by_type:
                criteria_by_type[service_type] = criterion_model._get_for_service_type(
                    service_type
                )
            criteria = criteria_by_type[service_type]
            current = assignment_model.get_for_employee_date(
                signup.employee_id, signup.service_id.service_date
            ).shift_type_id
            previous = assignment_model.get_for_employee_date(
                signup.employee_id,
                signup.service_id.service_date - timedelta(days=1),
            ).shift_type_id
            tier, status = signup._get_priority_tier(
                current, previous, set(criteria.mapped("criterion_type"))
            )
            signup.current_shift_id = current
            signup.previous_shift_id = previous
            signup.quarter_service_count = signup._quarter_service_count()
            signup.is_free = tier == "1_free"
            signup.priority_tier = tier
            signup.availability_status = status
            signup.priority_score = (
                signup._get_ranking_score(tier, criteria)
                if tier in ELIGIBLE_TIERS
                else EXCLUDED_PRIORITY
            )

    def _get_ranking_keys(self, tier):
        self.ensure_one()
        return {
            "rank_free": 0 if tier == "1_free" else 1,
            "rank_rest": 0 if tier in ("1_free", "2_rested") else 1,
            "rank_quarter_services": round(self.quarter_service_count * 100),
            "rank_signup_order": self.row_number,
        }

    def _get_ranking_score(self, tier, criteria):
        """Combina los criterios de orden activos en una única puntuación.

        Cada criterio ocupa un rango fijo (``RANKING_WIDTHS``), de modo que
        ordenar por la puntuación equivale a ordenar por los criterios en su
        secuencia: el primero decide y los siguientes desempatan.
        """
        keys = self._get_ranking_keys(tier)
        score = 0
        for criterion in criteria.filtered(lambda item: item.kind == "ranking"):
            width = RANKING_WIDTHS[criterion.criterion_type]
            score = score * width + min(keys[criterion.criterion_type], width - 1)
        return score

    @api.depends("priority_score", "service_id.signup_ids.priority_score")
    def _compute_rank(self):
        for signup in self:
            scores = [
                item.priority_score
                for item in signup.service_id.signup_ids
                if item.priority_score < EXCLUDED_PRIORITY
            ]
            signup.rank = (
                1 + sum(score < signup.priority_score for score in scores)
                if signup.priority_score < EXCLUDED_PRIORITY
                else 0
            )

    @api.depends("priority_tier")
    def _compute_state(self):
        for signup in self:
            if signup.state in RESOLVED_SIGNUP_STATES:
                continue
            if signup.priority_tier == "4_restricted":
                signup.state = "restricted"
            elif signup.priority_tier == "5_unavailable":
                signup.state = "unavailable"
            elif signup.priority_tier == "1_free":
                signup.state = "eligible_free"
            else:
                signup.state = "eligible_double"

    @api.depends("service_id.service_type_id.unit_price")
    def _compute_amount(self):
        for signup in self:
            signup.amount = signup.service_id.service_type_id.unit_price


class PoliceExtraServiceHistory(models.Model):
    _name = "almonte.police.extra.service.history"
    _description = "Histórico de servicios extraordinarios realizados"
    _order = "service_date desc, id desc"

    origin = fields.Selection(
        selection=[
            ("service", "Servicio adjudicado"),
            ("backlog", "Extra atrasado"),
        ],
        string="Origen",
        required=True,
        default="service",
        help="Los extras atrasados son servicios realizados antes de usar la "
        "aplicación (o no registrados en ella). Computan para el baremo con "
        "sus días y se incluyen en las liquidaciones con su cuantía.",
    )
    service_id = fields.Many2one(
        "almonte.police.extra.service",
        ondelete="cascade",
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Agente",
        required=True,
        ondelete="restrict",
        domain=[("police_category_id", "!=", False)],
    )
    service_date = fields.Date(string="Fecha del servicio", required=True, index=True)
    service_type_id = fields.Many2one(
        "almonte.police.service.type",
        string="Tipo de servicio",
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
        "res.currency",
        compute="_compute_currency_id",
        store=True,
        readonly=False,
        precompute=True,
    )
    notes = fields.Text(string="Observaciones")

    _employee_service_unique = models.Constraint(
        "UNIQUE(service_id, employee_id)",
        "El histórico del servicio para este agente ya existe.",
    )

    @api.depends("service_type_id.currency_id")
    def _compute_currency_id(self):
        for history in self:
            history.currency_id = (
                history.service_type_id.currency_id
                or history.currency_id
                or self.env.company.currency_id
            )

    @api.constrains("origin", "service_id", "service_type_id")
    def _check_origin_fields(self):
        for history in self:
            if history.origin == "service" and not (
                history.service_id and history.service_type_id
            ):
                raise ValidationError(
                    _(
                        "Un histórico de servicio adjudicado debe indicar el "
                        "servicio y su tipo."
                    )
                )

    @api.constrains("origin", "days", "amount")
    def _check_backlog_values(self):
        for history in self.filtered(lambda item: item.origin == "backlog"):
            if history.days <= 0:
                raise ValidationError(
                    _("Los días de un extra atrasado deben ser mayores que cero.")
                )
            if history.amount < 0:
                raise ValidationError(
                    _("La cuantía de los extras atrasados no puede ser negativa.")
                )

    def _get_baremo_weight(self):
        """Peso en el baremo: 1 por servicio adjudicado, los días si es atrasado."""
        self.ensure_one()
        return self.days if self.origin == "backlog" else 1.0
