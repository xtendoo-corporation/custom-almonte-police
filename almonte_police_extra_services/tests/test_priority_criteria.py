# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from datetime import date

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase

MODULE = "almonte_police_extra_services"


class TestAlmontePolicePriorityCriteria(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ref = cls.env.ref
        cls.criterion_model = cls.env["almonte.police.priority.criterion"]
        cls.category = ref(f"{MODULE}.police_category_police")
        cls.morning = ref(f"{MODULE}.shift_morning")
        cls.afternoon = ref(f"{MODULE}.shift_afternoon")
        cls.night = ref(f"{MODULE}.shift_night")
        cls.rest = ref(f"{MODULE}.shift_rest")
        cls.vacation = ref(f"{MODULE}.shift_vacation")
        service_types = cls.env["almonte.police.service.type"]
        cls.service_type = service_types.create(
            {"name": "Prelación general", "code": "PREG", "unit_price": 100}
        )
        cls.other_service_type = service_types.create(
            {"name": "Prelación propia", "code": "PREP", "unit_price": 100}
        )
        users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.coordinator_user = users.create(
            {
                "name": "Coordinador Prelación",
                "login": "coordinador.prelacion@example.com",
                "group_ids": [(6, 0, [ref(f"{MODULE}.group_police_extra_user").id])],
            }
        )
        cls.manager_user = users.create(
            {
                "name": "Responsable Prelación",
                "login": "responsable.prelacion@example.com",
                "group_ids": [(6, 0, [ref(f"{MODULE}.group_police_extra_manager").id])],
            }
        )
        cls.double_employee = cls._employee("Agente dobla", 9701)
        cls.free_employee = cls._employee("Agente libre", 9702)

    @classmethod
    def _employee(cls, name, number):
        return cls.env["hr.employee"].create(
            {
                "name": name,
                "police_employee_id": number,
                "police_dip": f"DIP-{number}",
                "police_category_id": cls.category.id,
            }
        )

    def _criterion(self, criterion_type, service_type=None):
        return self.criterion_model.search(
            [
                ("criterion_type", "=", criterion_type),
                ("service_type_id", "=", service_type.id if service_type else False),
            ]
        )

    def _schedule(self, employee, schedule_date, shift):
        return self.env["almonte.police.schedule.assignment"].create(
            {
                "employee_id": employee.id,
                "date": schedule_date,
                "shift_type_id": shift.id,
            }
        )

    def _service(self, service_date, shift, service_type=None):
        return self.env["almonte.police.extra.service"].create(
            {
                "service_date": service_date,
                "required_shift_id": shift.id,
                "service_type_id": (service_type or self.service_type).id,
                "vacancies": 1,
            }
        )

    def _signup(self, service, employee, row_number=0):
        return self.env["almonte.police.extra.service.signup"].create(
            {
                "service_id": service.id,
                "employee_id": employee.id,
                "row_number": row_number,
            }
        )

    def _double_vs_free(self, service_date, service_type=None):
        """Inscribe primero a quien dobla turno y después a quien está libre."""
        self._schedule(self.double_employee, service_date, self.morning)
        self._schedule(self.free_employee, service_date, self.rest)
        service = self._service(service_date, self.afternoon, service_type)
        double = self._signup(service, self.double_employee, 1)
        free = self._signup(service, self.free_employee, 2)
        service.action_publish()
        return service, double, free

    # Datos y configuración

    def test_default_criteria_are_installed(self):
        general = self.criterion_model.search([("service_type_id", "=", False)])
        self.assertEqual(len(general.filtered(lambda c: c.kind == "exclusion")), 5)
        self.assertEqual(
            general.filtered(lambda c: c.kind == "ranking")
            .sorted("sequence")
            .mapped("criterion_type"),
            [
                "rank_free",
                "rank_rest",
                "rank_quarter_services",
                "rank_signup_order",
            ],
        )
        self.assertTrue(all(general.mapped("enabled")))
        self.assertTrue(all(general.mapped("explanation")))

    def test_duplicate_criterion_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.criterion_model.create({"criterion_type": "rank_free"})

    def test_only_managers_can_change_criteria(self):
        criterion = self._criterion("rank_free")
        with self.assertRaises(AccessError):
            criterion.with_user(self.coordinator_user).write({"enabled": False})
        criterion.with_user(self.manager_user).write({"enabled": False})
        self.assertFalse(criterion.enabled)

    def test_priority_menu_is_in_configuration(self):
        menu = self.env.ref(f"{MODULE}.menu_priority_criteria")
        self.assertEqual(menu.parent_id, self.env.ref(f"{MODULE}.menu_configuration"))

    # Criterios de orden

    def test_default_order_prefers_free_agent(self):
        _service, double, free = self._double_vs_free(date(2027, 9, 6))
        self.assertEqual(free.rank, 1)
        self.assertEqual(double.rank, 2)
        self.assertEqual(free.priority_score, 2)

    def test_disabling_criteria_recomputes_open_services(self):
        _service, double, free = self._double_vs_free(date(2027, 9, 7))
        (self._criterion("rank_free") | self._criterion("rank_rest")).write(
            {"enabled": False}
        )
        self.assertEqual(double.rank, 1)
        self.assertEqual(free.rank, 2)

    def test_reordering_criteria_changes_assignment(self):
        service, double, free = self._double_vs_free(date(2027, 9, 8))
        self._criterion("rank_signup_order").sequence = 1
        self.assertEqual(double.rank, 1)
        service.action_assign()
        self.assertEqual(double.state, "assigned_double")
        self.assertEqual(free.state, "waiting_free")

    def test_ranking_score_clamps_each_key_to_its_width(self):
        _service, double, _free = self._double_vs_free(date(2027, 9, 9))
        criteria = self._criterion("rank_signup_order")
        double.row_number = 250000
        self.assertEqual(double._get_ranking_score("3_double", criteria), 99999)

    def test_quarter_services_criterion_uses_baremo(self):
        _service, double, _free = self._double_vs_free(date(2027, 9, 10))
        criteria = self._criterion("rank_quarter_services") | self._criterion(
            "rank_signup_order"
        )
        keys = double._get_ranking_keys("3_double")
        self.assertEqual(keys["rank_free"], 1)
        self.assertEqual(keys["rank_rest"], 1)
        self.assertEqual(
            double._get_ranking_score("3_double", criteria),
            keys["rank_quarter_services"] * 100000 + double.row_number,
        )

    def test_without_ranking_criteria_score_is_zero(self):
        _service, double, _free = self._double_vs_free(date(2027, 9, 13))
        self.criterion_model.search(
            [("kind", "=", "ranking"), ("service_type_id", "=", False)]
        ).write({"enabled": False})
        self.assertEqual(double.priority_score, 0)
        self.assertEqual(double.rank, 1)

    # Exclusiones

    def test_absence_exclusion_can_be_disabled(self):
        service_date = date(2027, 9, 14)
        self._schedule(self.free_employee, service_date, self.vacation)
        service = self._service(service_date, self.afternoon)
        signup = self._signup(service, self.free_employee)
        service.action_publish()
        self.assertEqual(signup.priority_tier, "5_unavailable")
        self._criterion("exclude_absence").enabled = False
        self.assertEqual(signup.priority_tier, "1_free")

    def test_no_schedule_exclusion_can_be_disabled(self):
        service = self._service(date(2027, 9, 15), self.afternoon)
        signup = self._signup(service, self.free_employee)
        service.action_publish()
        self.assertEqual(signup.priority_tier, "5_unavailable")
        self._criterion("exclude_no_schedule").enabled = False
        self.assertEqual(signup.priority_tier, "1_free")
        self.assertEqual(signup.availability_status, "LIBRE (sin cuadrante)")

    def test_overlap_exclusion_can_be_disabled(self):
        service_date = date(2027, 9, 16)
        self._schedule(self.double_employee, service_date, self.morning)
        service = self._service(service_date, self.morning)
        signup = self._signup(service, self.double_employee)
        service.action_publish()
        self.assertEqual(signup.priority_tier, "4_restricted")
        self._criterion("exclude_shift_overlap").enabled = False
        self.assertEqual(signup.priority_tier, "3_double")
        self.assertIn("coincide", signup.availability_status)

    def test_shift_restriction_exclusion_can_be_disabled(self):
        service_date = date(2027, 9, 17)
        self._schedule(self.free_employee, date(2027, 9, 16), self.night)
        self._schedule(self.free_employee, service_date, self.rest)
        service = self._service(service_date, self.morning)
        signup = self._signup(service, self.free_employee)
        service.action_publish()
        self.assertEqual(signup.priority_tier, "4_restricted")
        self._criterion("exclude_shift_restriction").enabled = False
        self.assertNotEqual(signup.priority_tier, "4_restricted")

    def test_already_assigned_exclusion_can_be_disabled(self):
        service_date = date(2027, 9, 20)
        self._schedule(self.free_employee, service_date, self.rest)
        first = self._service(service_date, self.afternoon)
        self._signup(first, self.free_employee)
        first.action_publish()
        first.action_assign()
        second = self._service(service_date, self.afternoon)
        signup = self._signup(second, self.free_employee)
        second.action_publish()
        self.assertEqual(signup.priority_tier, "4_restricted")
        self._criterion("exclude_already_assigned").enabled = False
        self.assertEqual(signup.priority_tier, "1_free")

    # Orden propio por tipo de servicio

    def test_service_type_can_customize_priority(self):
        self.assertFalse(self.other_service_type.uses_custom_priority)
        self.other_service_type.with_user(self.manager_user).action_customize_priority()
        self.other_service_type.action_customize_priority()
        custom = self.other_service_type.priority_criterion_ids
        self.assertTrue(self.other_service_type.uses_custom_priority)
        self.assertEqual(len(custom), 9)
        _general_service, general_double, general_free = self._double_vs_free(
            date(2027, 9, 21)
        )
        custom.filtered(lambda c: c.criterion_type in ("rank_free", "rank_rest")).write(
            {"enabled": False}
        )
        custom_service = self._service(
            date(2027, 9, 21), self.afternoon, self.other_service_type
        )
        custom_double = self._signup(custom_service, self.double_employee, 1)
        custom_free = self._signup(custom_service, self.free_employee, 2)
        custom_service.action_publish()
        self.assertEqual(custom_double.rank, 1)
        self.assertEqual(custom_free.rank, 2)
        self.assertEqual(general_free.rank, 1)
        self.assertEqual(general_double.rank, 2)

    def test_service_type_can_return_to_general_priority(self):
        self.other_service_type.action_customize_priority()
        action = self.other_service_type.action_reset_priority()
        self.assertEqual(action["tag"], "display_notification")
        self.assertFalse(self.other_service_type.priority_criterion_ids)
        self.assertEqual(
            self.criterion_model._get_for_service_type(self.other_service_type),
            self.criterion_model.search([("service_type_id", "=", False)]),
        )
