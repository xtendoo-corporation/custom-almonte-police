# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

EXCLUSION_TYPES = [
    ("exclude_no_schedule", "Excluir si no tiene cuadrante ese día"),
    ("exclude_absence", "Excluir si tiene una ausencia que impide el extra"),
    ("exclude_already_assigned", "Excluir si ya tiene otro extra adjudicado"),
    ("exclude_shift_restriction", "Excluir por restricción entre turnos"),
    ("exclude_shift_overlap", "Excluir si el extra coincide con su turno"),
]
RANKING_TYPES = [
    ("rank_free", "Primero quien está libre ese día"),
    ("rank_rest", "Primero quien tiene el descanso mínimo antes del extra"),
    ("rank_quarter_services", "Primero quien lleva menos servicios en el trimestre"),
    ("rank_signup_order", "Primero quien se inscribió antes"),
]
CRITERION_TYPES = EXCLUSION_TYPES + RANKING_TYPES
CRITERION_HELP = {
    "exclude_no_schedule": "Si se desactiva, quien no tiene cuadrante ese día "
    "se trata como libre.",
    "exclude_absence": "Vacaciones, asuntos propios, bajas, etc. Las ausencias "
    "parciales solo excluyen los turnos que tengan configurados.",
    "exclude_already_assigned": "Evita adjudicar dos extras que se solapan al "
    "mismo agente.",
    "exclude_shift_restriction": "Aplica las reglas de Configuración → "
    "Restricciones entre turnos (p. ej. Noche → Mañana).",
    "exclude_shift_overlap": "Si se desactiva, quien coincide con su turno "
    "se trata como si doblara turno.",
    "rank_free": "Libre, descanso o ausencia que no impide el extra.",
    "rank_rest": "Tiene al menos las horas de descanso mínimo configuradas "
    "antes del extra (incluye a quien está libre).",
    "rank_quarter_services": "Baremo del trimestre natural del servicio.",
    "rank_signup_order": "Desempate por orden de inscripción en la oferta.",
}
# Rango de valores de cada clave de orden para combinarlas en una puntuación.
RANKING_WIDTHS = {
    "rank_free": 2,
    "rank_rest": 2,
    "rank_quarter_services": 1_000_000,
    "rank_signup_order": 100_000,
}


class PolicePriorityCriterion(models.Model):
    _name = "almonte.police.priority.criterion"
    _description = "Criterio de prelación de servicios extraordinarios"
    _order = "service_type_id, kind, sequence, id"

    sequence = fields.Integer(string="Secuencia", default=10)
    criterion_type = fields.Selection(
        selection=CRITERION_TYPES,
        string="Criterio",
        required=True,
    )
    kind = fields.Selection(
        selection=[("exclusion", "Exclusión"), ("ranking", "Orden")],
        string="Tipo",
        compute="_compute_kind",
        store=True,
    )
    enabled = fields.Boolean(string="Activo", default=True)
    service_type_id = fields.Many2one(
        "almonte.police.service.type",
        string="Tipo de servicio",
        ondelete="cascade",
        help="Vacío: orden general para todos los servicios sin orden propio.",
    )
    explanation = fields.Char(string="Cómo funciona", compute="_compute_explanation")

    @api.depends("criterion_type")
    def _compute_kind(self):
        exclusion_codes = {code for code, _label in EXCLUSION_TYPES}
        for criterion in self:
            criterion.kind = (
                "exclusion"
                if criterion.criterion_type in exclusion_codes
                else "ranking"
            )

    @api.depends("criterion_type")
    def _compute_explanation(self):
        for criterion in self:
            criterion.explanation = CRITERION_HELP.get(criterion.criterion_type)

    @api.constrains("criterion_type", "service_type_id")
    def _check_unique_criterion(self):
        for criterion in self:
            duplicate = self.search_count(
                [
                    ("id", "!=", criterion.id),
                    ("criterion_type", "=", criterion.criterion_type),
                    ("service_type_id", "=", criterion.service_type_id.id),
                ]
            )
            if duplicate:
                raise ValidationError(
                    _(
                        "El criterio «%s» ya está configurado en este orden.",
                        dict(CRITERION_TYPES)[criterion.criterion_type],
                    )
                )

    @api.model
    def _get_for_service_type(self, service_type):
        """Criterios activos aplicables a un tipo de servicio.

        Si el tipo tiene un orden propio se usa ese, aunque tenga criterios
        desactivados; si no, el orden general.
        """
        criteria = self.sudo().search([("service_type_id", "=", service_type.id)])
        if not service_type or not criteria:
            criteria = self.sudo().search([("service_type_id", "=", False)])
        return criteria.filtered("enabled").sorted(
            lambda item: (item.sequence, item.id)
        )

    @api.model
    def _recompute_pending_signups(self):
        pending = self.env["almonte.police.extra.service.signup"].search(
            [("service_id.state", "=", "published")]
        )
        pending._compute_availability()
        pending._compute_state()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self._recompute_pending_signups()
        return records

    def write(self, vals):
        result = super().write(vals)
        self._recompute_pending_signups()
        return result

    def unlink(self):
        result = super().unlink()
        self._recompute_pending_signups()
        return result
