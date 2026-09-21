# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from datetime import date, timedelta
from unittest.mock import patch

import psycopg2

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase


class TestAlmontePoliceExtraServices(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env["hr.employee"].create(
            {
                "name": "Agente Almonte Test",
                "police_employee_id": 9101,
                "police_dip": "DIP-9101",
                "police_category": "police",
                "police_group": "Grupo 1",
            }
        )
        cls.morning = cls.env.ref("almonte_police_extra_services.shift_morning")
        cls.afternoon = cls.env.ref("almonte_police_extra_services.shift_afternoon")
        cls.night = cls.env.ref("almonte_police_extra_services.shift_night")
        cls.rest = cls.env.ref("almonte_police_extra_services.shift_rest")
        cls.free = cls.env.ref("almonte_police_extra_services.shift_unknown_l")
        cls.free.write({"name": "Libre", "state_kind": "rest"})
        cls.service_type = cls.env["almonte.police.service.type"].create(
            {
                "name": "Servicio de prueba",
                "code": "TEST",
                "pricing_mode": "hourly",
                "unit_price": 20,
            }
        )

    def _schedule(self, service_date, current, previous=None):
        schedule = self.env["almonte.police.schedule.assignment"]
        schedule.create(
            {
                "employee_id": self.employee.id,
                "date": service_date,
                "shift_type_id": current.id,
            }
        )
        if previous:
            schedule.create(
                {
                    "employee_id": self.employee.id,
                    "date": service_date - timedelta(days=1),
                    "shift_type_id": previous.id,
                }
            )

    def _service(self, service_date, hours=3):
        return self.env["almonte.police.extra.service"].create(
            {
                "service_date": service_date,
                "required_shift_id": self.morning.id,
                "service_type_id": self.service_type.id,
                "vacancies": 1,
                "hours": hours,
            }
        )

    def test_free_agent_is_prioritized_and_hourly_amount_is_computed(self):
        service_date = date(2026, 9, 12)
        self._schedule(service_date, self.rest)
        service = self._service(service_date)
        signup = self.env["almonte.police.extra.service.signup"].create(
            {
                "service_id": service.id,
                "employee_id": self.employee.id,
                "row_number": 1,
            }
        )
        self.assertEqual(signup.availability_status, "DISPONIBLE LIBRE")
        self.assertEqual(signup.priority_score, 1)
        self.assertEqual(signup.amount, 60)
        service.action_publish()
        service.action_assign()
        self.assertEqual(signup.state, "assigned_free")
        self.assertEqual(service.history_ids.employee_id, self.employee)

    def test_night_to_morning_is_restricted(self):
        service_date = date(2026, 9, 13)
        self._schedule(service_date, self.rest, self.night)
        service = self._service(service_date)
        signup = self.env["almonte.police.extra.service.signup"].create(
            {
                "service_id": service.id,
                "employee_id": self.employee.id,
            }
        )
        self.assertEqual(signup.priority_score, 99999)
        self.assertEqual(signup.state, "restricted")
        self.assertIn("Noche a Mañana", signup.availability_status)

    def test_restriction_catalog_controls_night_to_morning(self):
        service_date = date(2026, 9, 23)
        self._schedule(service_date, self.rest, self.night)
        service = self._service(service_date)
        custom_morning = self.env["almonte.police.shift.type"].create(
            {
                "name": "Mañana configurada",
                "code": "M_CFG",
                "state_kind": "work",
            }
        )
        service.required_shift_id = custom_morning
        signup = self.env["almonte.police.extra.service.signup"].create(
            {
                "service_id": service.id,
                "employee_id": self.employee.id,
            }
        )
        self.assertEqual(signup.state, "eligible_free")
        self.env["almonte.police.shift.restriction"].create(
            {
                "previous_shift_id": self.night.id,
                "required_shift_id": custom_morning.id,
                "reason": "Descanso configurado",
            }
        )
        signup._compute_availability()
        signup._compute_state()
        self.assertEqual(signup.state, "restricted")

    def test_historical_extra_days_reset_each_quarter(self):
        service_date = date(2026, 9, 24)
        service = self._service(service_date)
        previous_quarter_service = self._service(date(2026, 6, 30))
        current_quarter_service = self._service(date(2026, 7, 1))
        history_model = self.env["almonte.police.extra.service.history"]
        history_model.create(
            {
                "service_id": previous_quarter_service.id,
                "employee_id": self.employee.id,
                "service_date": date(2026, 6, 30),
                "service_type_id": self.service_type.id,
                "amount": 20,
                "days": 4,
            }
        )
        history_model.create(
            {
                "service_id": current_quarter_service.id,
                "employee_id": self.employee.id,
                "service_date": date(2026, 7, 1),
                "service_type_id": self.service_type.id,
                "amount": 20,
                "days": 2,
            }
        )
        signup = self.env["almonte.police.extra.service.signup"].create(
            {
                "service_id": service.id,
                "employee_id": self.employee.id,
            }
        )
        self.assertEqual(signup.accumulated_extra_days, 2)

    def test_work_shift_continuity_is_allowed(self):
        cases = (
            (self.morning, self.afternoon, "DISPONIBLE DOBLANDO"),
            (self.afternoon, self.night, "DISPONIBLE DOBLANDO"),
        )
        for index, (current, required, expected_status) in enumerate(cases):
            with self.subTest(current=current.code, required=required.code):
                service_date = date(2026, 9, 22 + index)
                self._schedule(service_date, current)
                service = self._service(service_date)
                service.required_shift_id = required
                signup = self.env["almonte.police.extra.service.signup"].create(
                    {
                        "service_id": service.id,
                        "employee_id": self.employee.id,
                    }
                )
                self.assertEqual(signup.availability_status, expected_status)
                self.assertLess(signup.priority_score, 99999)

    def test_all_absence_states_are_unavailable(self):
        absence_types = (
            self.env.ref("almonte_police_extra_services.shift_vacation"),
            self.env.ref("almonte_police_extra_services.shift_sick_leave"),
            self.env.ref("almonte_police_extra_services.shift_personal_leave"),
            self.env.ref("almonte_police_extra_services.shift_union_hours"),
            self.env.ref("almonte_police_extra_services.shift_permission"),
            self.env.ref("almonte_police_extra_services.shift_compensation_day"),
            self.env.ref("almonte_police_extra_services.shift_seniority_day"),
            self.env.ref("almonte_police_extra_services.shift_compensation_hours"),
            self.env.ref("almonte_police_extra_services.shift_family_leave"),
            self.env.ref("almonte_police_extra_services.shift_free_disposition"),
        )
        for index, absence in enumerate(absence_types):
            with self.subTest(absence=absence.code):
                service_date = date(2026, 10, 1 + index)
                self._schedule(service_date, absence)
                service = self._service(service_date)
                signup = self.env["almonte.police.extra.service.signup"].create(
                    {
                        "service_id": service.id,
                        "employee_id": self.employee.id,
                    }
                )
                self.assertEqual(signup.priority_score, 99999)
                self.assertEqual(signup.state, "unavailable")

    def test_l_code_is_free(self):
        service_date = date(2026, 9, 13)
        self._schedule(service_date, self.free)
        service = self._service(service_date)
        signup = self.env["almonte.police.extra.service.signup"].create(
            {
                "service_id": service.id,
                "employee_id": self.employee.id,
                "row_number": 1,
            }
        )
        self.assertTrue(signup.is_free)
        self.assertEqual(signup.availability_status, "DISPONIBLE LIBRE")

    def test_absence_is_not_available(self):
        absence = self.env["almonte.police.shift.type"].create(
            {
                "name": "Ausencia test",
                "code": "TEST_ABS",
                "state_kind": "absence",
            }
        )
        service_date = date(2026, 9, 14)
        self._schedule(service_date, absence)
        service = self._service(service_date)
        signup = self.env["almonte.police.extra.service.signup"].create(
            {
                "service_id": service.id,
                "employee_id": self.employee.id,
            }
        )
        self.assertEqual(signup.priority_score, 99999)
        self.assertTrue(signup.availability_status.startswith("NO DISPONIBLE"))

    def test_duplicate_schedule_and_signup_are_rejected(self):
        service_date = date(2026, 9, 15)
        self._schedule(service_date, self.rest)
        with self.assertRaises(psycopg2.errors.UniqueViolation):
            self._schedule(service_date, self.rest)
        service = self._service(service_date)
        values = {"service_id": service.id, "employee_id": self.employee.id}
        self.env["almonte.police.extra.service.signup"].create(values)
        with self.assertRaises(psycopg2.errors.UniqueViolation):
            self.env["almonte.police.extra.service.signup"].create(values)

    def test_cannot_assign_unpublished_offer(self):
        service = self._service(date(2026, 9, 16))
        with self.assertRaises(UserError):
            service.action_assign()

    def test_signup_row_number_is_incremented(self):
        second_employee = self.env["hr.employee"].create(
            {
                "name": "Segundo agente Almonte Test",
                "police_employee_id": 9102,
                "police_dip": "DIP-9102",
                "police_category": "police",
                "police_group": "Grupo 1",
            }
        )
        service = self._service(date(2026, 9, 17))
        signup_model = self.env["almonte.police.extra.service.signup"]
        first_signup = signup_model.create(
            {"service_id": service.id, "employee_id": self.employee.id}
        )
        second_signup = signup_model.create(
            {"service_id": service.id, "employee_id": second_employee.id}
        )
        self.assertEqual(first_signup.row_number, 1)
        self.assertEqual(second_signup.row_number, 2)

    def test_signup_opens_filtered_employee_schedule(self):
        service = self._service(date(2026, 9, 18))
        signup = self.env["almonte.police.extra.service.signup"].create(
            {
                "service_id": service.id,
                "employee_id": self.employee.id,
            }
        )
        action = signup.action_open_employee_schedule()
        self.assertEqual(action["res_model"], "almonte.police.schedule.assignment")
        self.assertEqual(
            action["domain"],
            [("employee_id", "=", self.employee.id)],
        )
        self.assertEqual(action["target"], "new")

    def test_deleting_service_deletes_generated_history(self):
        service_date = date(2026, 9, 19)
        self._schedule(service_date, self.rest)
        service = self._service(service_date)
        service.action_publish()
        signup = self.env["almonte.police.extra.service.signup"].create(
            {
                "service_id": service.id,
                "employee_id": self.employee.id,
            }
        )
        service.action_assign()
        history = self.env["almonte.police.extra.service.history"].search(
            [("service_id", "=", service.id)]
        )
        self.assertTrue(history)
        service.unlink()
        self.assertFalse(
            self.env["almonte.police.extra.service.history"].search(
                [("id", "=", history.id)]
            )
        )
        self.assertFalse(
            self.env["almonte.police.extra.service.signup"].search(
                [("id", "=", signup.id)]
            )
        )


class TestAlmontePoliceSelfService(TransactionCase):
    """Autoservicio de agentes: grupos, reglas de registro y notificaciones."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent_group = cls.env.ref(
            "almonte_police_extra_services.group_police_extra_agent"
        )
        cls.manager_group = cls.env.ref(
            "almonte_police_extra_services.group_police_extra_manager"
        )
        cls.morning = cls.env.ref("almonte_police_extra_services.shift_morning")
        cls.rest = cls.env.ref("almonte_police_extra_services.shift_rest")
        cls.service_type = cls.env["almonte.police.service.type"].create(
            {
                "name": "Servicio autoservicio",
                "code": "SELF",
                "pricing_mode": "hourly",
                "unit_price": 25,
            }
        )

        cls.agent_user = cls.env["res.users"].create(
            {
                "name": "Agente Autoservicio",
                "login": "agente.autoservicio@example.com",
                "email": "agente.autoservicio@example.com",
                "group_ids": [(6, 0, [cls.agent_group.id])],
            }
        )
        cls.agent_employee = cls.env["hr.employee"].create(
            {
                "name": "Agente Autoservicio",
                "police_employee_id": 9201,
                "police_dip": "DIP-9201",
                "police_category": "police",
                "work_email": "agente.autoservicio@example.com",
                "user_id": cls.agent_user.id,
            }
        )

        cls.other_employee = cls.env["hr.employee"].create(
            {
                "name": "Otro Agente",
                "police_employee_id": 9202,
                "police_dip": "DIP-9202",
                "police_category": "police",
            }
        )

        cls.manager_user = cls.env["res.users"].create(
            {
                "name": "Responsable Policía",
                "login": "responsable.policia@example.com",
                "email": "responsable.policia@example.com",
                "group_ids": [(6, 0, [cls.manager_group.id])],
            }
        )

    def _service(self, service_date, hours=4, vacancies=1):
        return self.env["almonte.police.extra.service"].create(
            {
                "service_date": service_date,
                "required_shift_id": self.morning.id,
                "service_type_id": self.service_type.id,
                "vacancies": vacancies,
                "hours": hours,
            }
        )

    def test_agent_cannot_see_draft_service(self):
        service = self._service(date(2026, 9, 20))
        services = (
            self.env["almonte.police.extra.service"]
            .with_user(self.agent_user)
            .search([("id", "=", service.id)])
        )
        self.assertFalse(services)

    def test_agent_cannot_see_closed_service_without_signup(self):
        service = self._service(date(2026, 9, 30))
        service.action_publish()
        service.write({"state": "closed"})
        services = (
            self.env["almonte.police.extra.service"]
            .with_user(self.agent_user)
            .search([("id", "=", service.id)])
        )
        self.assertFalse(services)

    def test_agent_can_volunteer_on_published_service(self):
        service_date = date(2026, 9, 21)
        self.env["almonte.police.schedule.assignment"].create(
            {
                "employee_id": self.agent_employee.id,
                "date": service_date,
                "shift_type_id": self.rest.id,
            }
        )
        service = self._service(service_date)
        service.action_publish()
        service_as_agent = service.with_user(self.agent_user)
        self.assertTrue(service_as_agent.can_volunteer)
        service_as_agent.action_volunteer()
        signup = self.env["almonte.police.extra.service.signup"].search(
            [
                ("service_id", "=", service.id),
                ("employee_id", "=", self.agent_employee.id),
            ]
        )
        self.assertTrue(signup)
        self.assertFalse(service_as_agent.can_volunteer)

    def test_agent_cannot_volunteer_twice(self):
        service_date = date(2026, 9, 22)
        service = self._service(service_date)
        service.action_publish()
        service_as_agent = service.with_user(self.agent_user)
        service_as_agent.action_volunteer()
        with self.assertRaises(UserError):
            service_as_agent.action_volunteer()

    def test_agent_cannot_volunteer_on_draft_service(self):
        service = self._service(date(2026, 9, 23))
        with self.assertRaises(UserError):
            service.with_user(self.agent_user).action_volunteer()

    def test_agent_cannot_create_signup_directly_on_draft_service(self):
        service = self._service(date(2026, 9, 23))
        with self.assertRaises(UserError):
            self.env["almonte.police.extra.service.signup"].with_user(
                self.agent_user
            ).create(
                {
                    "service_id": service.id,
                    "employee_id": self.agent_employee.id,
                }
            )

    def test_agent_cannot_create_signup_for_another_employee(self):
        service = self._service(date(2026, 9, 24))
        service.action_publish()
        with self.assertRaises(AccessError):
            self.env["almonte.police.extra.service.signup"].with_user(
                self.agent_user
            ).create(
                {
                    "service_id": service.id,
                    "employee_id": self.other_employee.id,
                }
            )

    def test_agent_can_withdraw_before_resolution(self):
        service = self._service(date(2026, 9, 25))
        service.action_publish()
        service_as_agent = service.with_user(self.agent_user)
        service_as_agent.action_volunteer()
        signup = self.env["almonte.police.extra.service.signup"].search(
            [
                ("service_id", "=", service.id),
                ("employee_id", "=", self.agent_employee.id),
            ]
        )
        signup.with_user(self.agent_user).action_withdraw()
        self.assertFalse(signup.exists())

    def test_agent_cannot_withdraw_other_agent_signup(self):
        service = self._service(date(2026, 9, 26))
        signup = self.env["almonte.police.extra.service.signup"].create(
            {"service_id": service.id, "employee_id": self.other_employee.id}
        )
        with self.assertRaises(AccessError):
            signup.with_user(self.agent_user).action_withdraw()

    def test_resolution_email_sent_when_assigned(self):
        service_date = date(2026, 9, 27)
        self.env["almonte.police.schedule.assignment"].create(
            {
                "employee_id": self.agent_employee.id,
                "date": service_date,
                "shift_type_id": self.rest.id,
            }
        )
        service = self._service(service_date, vacancies=1)
        service.action_publish()
        signup = self.env["almonte.police.extra.service.signup"].create(
            {"service_id": service.id, "employee_id": self.agent_employee.id}
        )
        mail_before = self.env["mail.mail"].search_count(
            [
                ("model", "=", "almonte.police.extra.service.signup"),
                ("res_id", "=", signup.id),
            ]
        )
        service.action_assign()
        mail_after = self.env["mail.mail"].search(
            [
                ("model", "=", "almonte.police.extra.service.signup"),
                ("res_id", "=", signup.id),
            ]
        )
        self.assertEqual(signup.state, "assigned_free")
        self.assertEqual(len(mail_after), mail_before + 1)
        self.assertEqual(mail_after.email_to, self.agent_employee.work_email)

    def test_resolution_email_sent_when_waiting(self):
        service_date = date(2026, 9, 28)
        self.env["almonte.police.schedule.assignment"].create(
            {
                "employee_id": self.agent_employee.id,
                "date": service_date,
                "shift_type_id": self.rest.id,
            }
        )
        self.env["almonte.police.schedule.assignment"].create(
            {
                "employee_id": self.other_employee.id,
                "date": service_date,
                "shift_type_id": self.rest.id,
            }
        )
        service = self._service(service_date, vacancies=1)
        service.action_publish()
        self.env["almonte.police.extra.service.signup"].create(
            {
                "service_id": service.id,
                "employee_id": self.other_employee.id,
                "row_number": 1,
            }
        )
        agent_signup = self.env["almonte.police.extra.service.signup"].create(
            {
                "service_id": service.id,
                "employee_id": self.agent_employee.id,
                "row_number": 2,
            }
        )
        service.action_assign()
        self.assertEqual(agent_signup.state, "waiting_free")
        mail = self.env["mail.mail"].search(
            [
                ("model", "=", "almonte.police.extra.service.signup"),
                ("res_id", "=", agent_signup.id),
            ]
        )
        self.assertTrue(mail)

    def test_manager_can_create_police_user(self):
        employee = self.env["hr.employee"].create(
            {
                "name": "Nuevo Agente",
                "police_employee_id": 9203,
                "police_dip": "DIP-9203",
                "police_category": "police",
                "work_email": "nuevo.agente@example.com",
            }
        )
        with patch.object(
            type(self.env["res.users"]), "action_reset_password"
        ) as mock_reset:
            employee.with_user(self.manager_user).action_create_police_user()
        mock_reset.assert_called_once()
        self.assertTrue(employee.user_id)
        self.assertIn(self.agent_group, employee.user_id.group_ids)

    def test_non_manager_cannot_create_police_user(self):
        employee = self.env["hr.employee"].create(
            {
                "name": "Otro Nuevo Agente",
                "police_employee_id": 9204,
                "police_dip": "DIP-9204",
                "police_category": "police",
                "work_email": "otro.nuevo.agente@example.com",
            }
        )
        with self.assertRaises(AccessError):
            employee.with_user(self.agent_user).action_create_police_user()

    def test_coordinator_and_manager_keep_full_access(self):
        service = self._service(date(2026, 9, 29))
        services = (
            self.env["almonte.police.extra.service"]
            .with_user(self.manager_user)
            .search([("id", "=", service.id)])
        )
        self.assertTrue(services)
