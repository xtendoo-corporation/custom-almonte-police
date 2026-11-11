# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

COORDINATOR_GROUP = "almonte_police_extra_services.group_police_extra_user"
MAX_REQUEST_DAYS = 366


class PoliceAbsenceRequest(models.Model):
    _name = "almonte.police.absence.request"
    _description = "Solicitud de ausencia de agente"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_from desc, id desc"

    name = fields.Char(string="Descripción", compute="_compute_name", store=True)
    employee_id = fields.Many2one(
        "hr.employee",
        string="Agente",
        required=True,
        ondelete="cascade",
        index=True,
        domain=[("police_category_id", "!=", False)],
        default=lambda self: self.env.user.employee_id,
        tracking=True,
    )
    shift_type_id = fields.Many2one(
        "almonte.police.shift.type",
        string="Tipo",
        required=True,
        ondelete="restrict",
        domain=[("agent_requestable", "=", True)],
        tracking=True,
    )
    date_from = fields.Date(string="Desde", required=True, tracking=True)
    date_to = fields.Date(string="Hasta", required=True, tracking=True)
    day_count = fields.Integer(string="Días", compute="_compute_day_count")
    reason = fields.Text(string="Motivo")
    response = fields.Text(string="Respuesta del responsable")
    state = fields.Selection(
        selection=[
            ("pending", "Pendiente"),
            ("approved", "Aprobada"),
            ("refused", "Rechazada"),
            ("cancelled", "Cancelada"),
        ],
        string="Estado",
        default="pending",
        required=True,
        readonly=True,
        tracking=True,
    )

    @api.depends("employee_id", "shift_type_id", "date_from", "date_to")
    def _compute_name(self):
        for request in self:
            if not (request.employee_id and request.shift_type_id):
                request.name = _("Nueva solicitud")
                continue
            period = request.date_from and request.date_from.strftime("%d/%m/%Y")
            if request.date_to and request.date_to != request.date_from:
                period = f"{period} - {request.date_to.strftime('%d/%m/%Y')}"
            request.name = (
                f"{request.employee_id.name} · {request.shift_type_id.name} · {period}"
            )

    @api.depends("date_from", "date_to")
    def _compute_day_count(self):
        for request in self:
            request.day_count = (
                (request.date_to - request.date_from).days + 1
                if request.date_from and request.date_to
                else 0
            )

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for request in self:
            if request.date_to < request.date_from:
                raise ValidationError(
                    _("La fecha final no puede ser anterior a la inicial.")
                )
            if request.day_count > MAX_REQUEST_DAYS:
                raise ValidationError(
                    _("Una solicitud no puede superar los %s días.", MAX_REQUEST_DAYS)
                )

    @api.constrains("shift_type_id")
    def _check_shift_type_requestable(self):
        for request in self:
            if not request.shift_type_id.agent_requestable:
                raise ValidationError(
                    _(
                        "El tipo %s no se puede solicitar desde el autoservicio.",
                        request.shift_type_id.name,
                    )
                )

    def _is_coordinator(self):
        return self.env.user.has_group(COORDINATOR_GROUP)

    @api.model_create_multi
    def create(self, vals_list):
        if not self._is_coordinator():
            employee = self.env.user.employee_id
            if not employee:
                raise UserError(
                    _(
                        "Tu usuario no está vinculado a ningún agente de la "
                        "Policía Local."
                    )
                )
            for vals in vals_list:
                if vals.get("employee_id", employee.id) != employee.id:
                    raise AccessError(_("Solo puedes crear solicitudes propias."))
                if vals.get("state", "pending") != "pending":
                    raise AccessError(_("Las solicitudes se crean pendientes."))
                vals.pop("response", None)
        return super().create(vals_list)

    def _check_can_resolve(self):
        if not self._is_coordinator():
            raise AccessError(
                _("Solo coordinadores y responsables pueden resolver solicitudes.")
            )
        for request in self:
            if request.state != "pending":
                raise UserError(_("Solo se pueden resolver solicitudes pendientes."))

    def action_approve(self):
        """Aprueba la solicitud y aplica el estado a cada día del cuadrante."""
        self._check_can_resolve()
        assignment_model = self.env["almonte.police.schedule.assignment"]
        for request in self:
            dates = [
                request.date_from + timedelta(days=offset)
                for offset in range(request.day_count)
            ]
            existing = assignment_model.search(
                [
                    ("employee_id", "=", request.employee_id.id),
                    ("date", "in", dates),
                ]
            )
            existing.write({"shift_type_id": request.shift_type_id.id})
            existing_dates = set(existing.mapped("date"))
            assignment_model.create(
                [
                    {
                        "employee_id": request.employee_id.id,
                        "date": request_date,
                        "shift_type_id": request.shift_type_id.id,
                        "notes": request.reason,
                    }
                    for request_date in dates
                    if request_date not in existing_dates
                ]
            )
            request.state = "approved"
            request._notify_employee(_("aprobada"))

    def action_refuse(self):
        self._check_can_resolve()
        self.write({"state": "refused"})
        for request in self:
            request._notify_employee(_("rechazada"))

    def action_cancel(self):
        """El agente cancela su propia solicitud pendiente.

        Se usa ``sudo`` de forma controlada porque el agente no tiene permiso
        de escritura; la propiedad y el estado se comprueban antes.
        """
        for request in self:
            if (
                request.employee_id.user_id != self.env.user
                and not self._is_coordinator()
            ):
                raise AccessError(_("Solo puedes cancelar tus propias solicitudes."))
            if request.state != "pending":
                raise UserError(_("Solo se pueden cancelar solicitudes pendientes."))
        self.sudo().write({"state": "cancelled"})

    def _notify_employee(self, result):
        self.ensure_one()
        partner = self.employee_id.user_id.partner_id
        if partner:
            self.message_post(
                body=_(
                    "Tu solicitud de %(type)s ha sido %(result)s.",
                    type=self.shift_type_id.name,
                    result=result,
                ),
                partner_ids=partner.ids,
            )
