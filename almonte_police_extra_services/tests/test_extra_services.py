# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0)

from datetime import date, timedelta
from unittest.mock import patch

import psycopg2
from lxml import etree

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

from ..models.extra_service import EXCLUDED_PRIORITY


class TestAlmontePoliceExtraServices(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env["hr.employee"].create(
            {
                "name": "Agente Almonte Test",
                "police_employee_id": 9101,
                "police_dip": "DIP-9101",
                "police_category_id": cls.env.ref(
                    "almonte_police_extra_services.police_category_police"
                ).id,
                "police_group_id": cls.env.ref(
                    "almonte_police_extra_services.police_group_group_1"
                ).id,
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
                "unit_price": 20,
            }
        )

    def _schedule(self, service_date, current, previous=None, employee=None):
        employee = employee or self.employee
        schedule = self.env["almonte.police.schedule.assignment"]
        schedule.create(
            {
                "employee_id": employee.id,
                "date": service_date,
                "shift_type_id": current.id,
            }
        )
        if previous:
            schedule.create(
                {
                    "employee_id": employee.id,
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

    def test_free_agent_is_prioritized_and_total_amount_is_computed(self):
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
        self.assertEqual(signup.availability_status, "LIBRE")
        self.assertEqual(signup.priority_score, 1)
        self.assertEqual(signup.amount, 20)
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
        self.assertEqual(signup.priority_score, EXCLUDED_PRIORITY)
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

    def test_quarter_service_count_resets_each_quarter(self):
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
        self.assertEqual(signup.quarter_service_count, 1)

    def test_work_shift_continuity_is_allowed(self):
        cases = (
            (self.morning, self.afternoon, "DOBLA TURNO"),
            (self.afternoon, self.night, "DOBLA TURNO"),
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
                self.assertLess(signup.priority_score, EXCLUDED_PRIORITY)

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
                self.assertEqual(signup.priority_score, EXCLUDED_PRIORITY)
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
        self.assertEqual(signup.availability_status, "LIBRE")

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
        self.assertEqual(signup.priority_score, EXCLUDED_PRIORITY)
        self.assertTrue(signup.availability_status.startswith("NO DISPONIBLE"))

    def _signup(self, service, employee=None, row_number=0):
        return self.env["almonte.police.extra.service.signup"].create(
            {
                "service_id": service.id,
                "employee_id": (employee or self.employee).id,
                "row_number": row_number,
            }
        )

    def _other_employee(self, number):
        return self.env["hr.employee"].create(
            {
                "name": f"Agente prelación {number}",
                "police_employee_id": number,
                "police_category_id": self.employee.police_category_id.id,
            }
        )

    def test_shift_hours_are_configured(self):
        self.assertEqual(self.morning._get_interval(), (7.0, 15.0))
        self.assertEqual(self.afternoon._get_interval(), (15.0, 23.0))
        self.assertEqual(self.night._get_interval(), (23.0, 31.0))
        self.assertEqual(self.night._get_interval(day_offset=-1), (-1.0, 7.0))
        self.assertIsNone(self.rest._get_interval())

    def test_shift_hours_must_be_valid(self):
        with self.assertRaises(ValidationError):
            self.morning.end_hour = 24

    def test_priority_tiers_by_shift_combination(self):
        cases = (
            # (turno del día, turno del día anterior, servicio, nivel, estado)
            (self.rest, None, self.morning, "1_free", "eligible_free"),
            (self.morning, None, self.night, "2_rested", "eligible_double"),
            (self.morning, self.afternoon, self.night, "2_rested", "eligible_double"),
            (self.morning, None, self.afternoon, "3_double", "eligible_double"),
            (self.afternoon, None, self.night, "3_double", "eligible_double"),
            (self.night, None, self.afternoon, "3_double", "eligible_double"),
            (self.afternoon, None, self.morning, "3_double", "eligible_double"),
            (self.rest, self.night, self.morning, "4_restricted", "restricted"),
            (self.morning, None, self.morning, "4_restricted", "restricted"),
            (self.night, self.night, self.morning, "4_restricted", "restricted"),
        )
        for index, (current, previous, required, tier, state) in enumerate(cases):
            with self.subTest(
                current=current.code,
                previous=previous and previous.code,
                required=required.code,
            ):
                service_date = date(2027, 1, 10 + index * 2)
                self._schedule(service_date, current, previous)
                service = self._service(service_date)
                service.required_shift_id = required
                signup = self._signup(service)
                self.assertEqual(signup.priority_tier, tier)
                self.assertEqual(signup.state, state)
                self.assertEqual(signup.is_free, tier == "1_free")

    def test_missing_schedule_is_unavailable(self):
        signup = self._signup(self._service(date(2027, 3, 1)))
        self.assertEqual(signup.priority_tier, "5_unavailable")
        self.assertEqual(signup.state, "unavailable")
        self.assertEqual(signup.priority_score, EXCLUDED_PRIORITY)
        self.assertEqual(signup.rank, 0)

    def test_shift_without_hours_counts_as_double(self):
        no_hours = self.env["almonte.police.shift.type"].create(
            {"name": "Turno sin horario", "code": "SIN_H", "state_kind": "work"}
        )
        service_date = date(2027, 3, 2)
        self._schedule(service_date, no_hours)
        signup = self._signup(self._service(service_date))
        self.assertEqual(signup.priority_tier, "3_double")

    def test_assignment_follows_priority_order(self):
        service_date = date(2027, 3, 3)
        double = self._other_employee(9201)
        rested = self._other_employee(9202)
        self._schedule(service_date, self.rest)
        self._schedule(service_date, self.afternoon, employee=double)
        self._schedule(service_date, self.morning, employee=rested)
        service = self._service(service_date)
        service.write({"required_shift_id": self.night.id, "vacancies": 2})
        signups = (
            self._signup(service, double, row_number=1)
            + self._signup(service, rested, row_number=2)
            + self._signup(service, row_number=3)
        )
        service.action_publish()
        service.action_assign()
        self.assertEqual(
            signups.mapped("state"),
            ["waiting_double", "assigned_double", "assigned_free"],
        )
        self.assertEqual(signups.mapped("rank"), [3, 2, 1])
        self.assertEqual(
            signups.mapped("priority_tier"), ["3_double", "2_rested", "1_free"]
        )

    def test_fewer_quarter_services_then_signup_order_break_ties(self):
        service_date = date(2027, 3, 20)
        veteran = self._other_employee(9203)
        late = self._other_employee(9204)
        for employee in (self.employee, veteran, late):
            self._schedule(service_date, self.rest, employee=employee)
        past_service = self._service(date(2027, 1, 5))
        self.env["almonte.police.extra.service.history"].create(
            {
                "service_id": past_service.id,
                "employee_id": veteran.id,
                "service_date": date(2027, 1, 5),
                "service_type_id": self.service_type.id,
                "amount": 20,
                "days": 1,
            }
        )
        service = self._service(service_date)
        veteran_signup = self._signup(service, veteran, row_number=1)
        first_signup = self._signup(service, row_number=2)
        late_signup = self._signup(service, late, row_number=3)
        self.assertEqual(veteran_signup.quarter_service_count, 1)
        self.assertEqual(first_signup.rank, 1)
        self.assertEqual(late_signup.rank, 2)
        self.assertEqual(veteran_signup.rank, 3)

    def test_assignment_recomputes_availability_after_schedule_change(self):
        service_date = date(2027, 3, 25)
        self._schedule(service_date, self.rest)
        service = self._service(service_date)
        signup = self._signup(service)
        self.assertEqual(signup.state, "eligible_free")
        self.env["almonte.police.schedule.assignment"].search(
            [("employee_id", "=", self.employee.id), ("date", "=", service_date)]
        ).shift_type_id = self.env.ref("almonte_police_extra_services.shift_vacation")
        service.action_publish()
        service.action_assign()
        self.assertEqual(signup.state, "unavailable")
        self.assertFalse(service.history_ids)

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
                "police_category_id": self.env.ref(
                    "almonte_police_extra_services.police_category_police"
                ).id,
                "police_group_id": self.env.ref(
                    "almonte_police_extra_services.police_group_group_1"
                ).id,
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
                "police_category_id": cls.env.ref(
                    "almonte_police_extra_services.police_category_police"
                ).id,
                "work_email": "agente.autoservicio@example.com",
                "user_id": cls.agent_user.id,
            }
        )

        cls.other_employee = cls.env["hr.employee"].create(
            {
                "name": "Otro Agente",
                "police_employee_id": 9202,
                "police_dip": "DIP-9202",
                "police_category_id": cls.env.ref(
                    "almonte_police_extra_services.police_category_police"
                ).id,
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

    def test_agent_sees_assignment_of_any_service_and_its_signups(self):
        service = self._service(date(2026, 9, 30))
        service.action_publish()
        other_signup = self.env["almonte.police.extra.service.signup"].create(
            {"service_id": service.id, "employee_id": self.other_employee.id}
        )
        service.write({"state": "closed"})
        agent_service = service.with_user(self.agent_user)
        self.assertEqual(agent_service.search([("id", "=", service.id)]), agent_service)
        self.assertEqual(agent_service.signup_ids, other_signup)
        self.assertEqual(
            agent_service.signup_ids.read(["employee_id"])[0]["employee_id"][0],
            self.other_employee.id,
        )
        with self.assertRaises(AccessError):
            other_signup.with_user(self.agent_user).write({"row_number": 99})
        with self.assertRaises(AccessError):
            other_signup.with_user(self.agent_user).unlink()

    def test_agent_cannot_see_signups_of_draft_or_other_category_service(self):
        draft = self._service(date(2026, 9, 29))
        restricted = self._service(date(2026, 9, 29))
        restricted.action_publish()
        signups = self.env["almonte.police.extra.service.signup"]
        for service in (draft, restricted):
            signups |= signups.create(
                {"service_id": service.id, "employee_id": self.other_employee.id}
            )
        inspector = self.env["almonte.police.category"].create(
            {"name": "Categoría exclusiva"}
        )
        restricted.write({"police_category_ids": [(6, 0, inspector.ids)]})
        self.assertFalse(
            signups.with_user(self.agent_user).search([("id", "in", signups.ids)])
        )

    def test_agent_only_sees_own_schedule_with_shift_hours(self):
        schedule = self.env["almonte.police.schedule.assignment"]
        own = schedule.create(
            {
                "employee_id": self.agent_employee.id,
                "date": date(2026, 11, 11),
                "shift_type_id": self.morning.id,
            }
        )
        schedule.create(
            {
                "employee_id": self.other_employee.id,
                "date": date(2026, 11, 11),
                "shift_type_id": self.rest.id,
            }
        )
        action = self.env.ref("almonte_police_extra_services.action_my_schedule")
        agent_schedule = schedule.with_user(self.agent_user).with_context(
            show_shift_hours=True
        )
        visible = agent_schedule.search([("date", "=", date(2026, 11, 11))])
        self.assertEqual(visible, own)
        self.assertEqual(visible.shift_type_id.display_name, "M: Mañana (07:00-15:00)")
        self.assertEqual(
            self.rest.with_context(show_shift_hours=True).display_name,
            f"{self.rest.code}: {self.rest.name}",
        )
        self.assertEqual(self.morning.display_name, "Mañana")
        self.assertIn("show_shift_hours", action.context)
        with self.assertRaises(AccessError):
            visible.write({"shift_type_id": self.rest.id})

    def test_agent_only_sees_own_confirmed_settlement(self):
        service_type = self.service_type
        histories = self.env["almonte.police.extra.service.history"]
        for employee in (self.agent_employee, self.other_employee):
            service = self._service(date(2026, 10, 5))
            histories |= histories.create(
                {
                    "service_id": service.id,
                    "employee_id": employee.id,
                    "service_date": date(2026, 10, 5),
                    "service_type_id": service_type.id,
                    "amount": 25,
                }
            )
        settlement = self.env["almonte.police.extra.settlement"].create(
            {"period_type": "month", "month": "10", "year": 2026}
        )
        settlement.action_generate()
        line_model = self.env["almonte.police.extra.settlement.line"].with_user(
            self.agent_user
        )
        self.assertFalse(line_model.search([]))
        settlement.action_confirm()
        own_line = line_model.search([])
        self.assertEqual(own_line.employee_id, self.agent_employee)
        self.assertEqual(own_line.total_amount, 25)
        self.assertEqual(own_line.settlement_name, settlement.name)
        self.assertEqual(own_line.history_ids.employee_id, self.agent_employee)
        with self.assertRaises(AccessError):
            settlement.with_user(self.agent_user).read(["total_amount"])

    def test_agent_menus_are_available(self):
        menus = self.env["ir.ui.menu"].with_user(self.agent_user)._visible_menu_ids()
        for xmlid in (
            "menu_my_schedule",
            "menu_extra_services_assignments",
            "menu_my_settlements",
            "menu_extra_services_available",
            "menu_my_signups",
        ):
            with self.subTest(menu=xmlid):
                self.assertIn(
                    self.env.ref(f"almonte_police_extra_services.{xmlid}").id, menus
                )
        self.assertNotIn(
            self.env.ref("almonte_police_extra_services.menu_extra_settlements").id,
            menus,
        )

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

    def _category(self, key):
        return self.env.ref(f"almonte_police_extra_services.police_category_{key}")

    def _agent_signup(self, service):
        return self.env["almonte.police.extra.service.signup"].search(
            [
                ("service_id", "=", service.id),
                ("employee_id", "=", self.agent_employee.id),
            ]
        )

    def test_agent_can_volunteer_when_category_is_allowed(self):
        service = self._service(date(2026, 10, 1))
        service.police_category_ids = self._category("inspector") | self._category(
            "police"
        )
        service.action_publish()
        service_as_agent = service.with_user(self.agent_user)
        self.assertTrue(service_as_agent.can_volunteer)
        service_as_agent.action_volunteer()
        self.assertTrue(self._agent_signup(service))

    def test_agent_cannot_see_or_join_service_of_other_category(self):
        service = self._service(date(2026, 10, 2))
        service.police_category_ids = self._category("inspector")
        service.action_publish()
        service_model = self.env["almonte.police.extra.service"].with_user(
            self.agent_user
        )
        self.assertFalse(service_model.search([("id", "=", service.id)]))
        self.assertFalse(service.with_user(self.agent_user).can_volunteer)
        with self.assertRaises(UserError):
            service.with_user(self.agent_user).action_volunteer()

    def test_coordinator_cannot_add_agent_of_other_category(self):
        service = self._service(date(2026, 10, 3))
        service.police_category_ids = self._category("inspector")
        with self.assertRaises(ValidationError):
            self.env["almonte.police.extra.service.signup"].create(
                {"service_id": service.id, "employee_id": self.agent_employee.id}
            )

    def test_agent_can_volunteer_before_signup_deadline(self):
        service = self._service(date(2099, 1, 15))
        service.signup_deadline = "2099-01-10 14:00:00"
        service.action_publish()
        service_as_agent = service.with_user(self.agent_user)
        self.assertTrue(service_as_agent.is_signup_open)
        self.assertTrue(service_as_agent.can_volunteer)
        service_as_agent.action_volunteer()
        self.assertTrue(self._agent_signup(service))

    def test_agent_cannot_volunteer_after_signup_deadline(self):
        service = self._service(date(2026, 10, 4))
        service.signup_deadline = "2026-10-01 14:00:00"
        service.action_publish()
        service_as_agent = service.with_user(self.agent_user)
        self.assertFalse(service_as_agent.is_signup_open)
        self.assertFalse(service_as_agent.can_volunteer)
        with self.assertRaises(UserError):
            service_as_agent.action_volunteer()
        with self.assertRaises(UserError):
            self.env["almonte.police.extra.service.signup"].with_user(
                self.agent_user
            ).create({"service_id": service.id, "employee_id": self.agent_employee.id})

    def test_agent_cannot_withdraw_after_signup_deadline(self):
        service = self._service(date(2026, 10, 5))
        service.action_publish()
        service.with_user(self.agent_user).action_volunteer()
        service.signup_deadline = "2026-10-01 14:00:00"
        signup = self._agent_signup(service)
        with self.assertRaises(UserError):
            signup.with_user(self.agent_user).action_withdraw()
        self.assertTrue(signup.exists())

    def test_signup_deadline_cannot_be_after_service_date(self):
        service = self._service(date(2026, 10, 6))
        with self.assertRaises(ValidationError):
            service.signup_deadline = "2026-10-08 10:00:00"

    def test_amount_is_total_service_price_regardless_of_hours(self):
        service = self._service(date(2026, 10, 7), hours=8)
        signup = self.env["almonte.police.extra.service.signup"].create(
            {"service_id": service.id, "employee_id": self.agent_employee.id}
        )
        self.assertEqual(signup.amount, self.service_type.unit_price)
        service.hours = 2
        self.assertEqual(signup.amount, self.service_type.unit_price)

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
                "police_category_id": self.env.ref(
                    "almonte_police_extra_services.police_category_police"
                ).id,
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
                "police_category_id": self.env.ref(
                    "almonte_police_extra_services.police_category_police"
                ).id,
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


class TestAlmontePoliceMenu(TransactionCase):
    def test_root_menu_shows_app_icon(self):
        root_menu = self.env.ref("almonte_police_extra_services.menu_police_root")
        self.assertEqual(
            root_menu.web_icon,
            "almonte_police_extra_services,static/description/icon.png",
        )
        self.assertTrue(root_menu.web_icon_data)

    def _menu(self, xml_id):
        return self.env.ref(f"almonte_police_extra_services.{xml_id}")

    def _assert_children(self, parent_xml_id, child_xml_ids):
        parent = self._menu(parent_xml_id)
        for child_xml_id in child_xml_ids:
            with self.subTest(menu=child_xml_id):
                self.assertEqual(self._menu(child_xml_id).parent_id, parent)

    def test_top_level_menus(self):
        root_menu = self._menu("menu_police_root")
        top_menus = self.env["ir.ui.menu"].search([("parent_id", "=", root_menu.id)])
        self.assertEqual(
            set(top_menus.mapped("name")),
            {"Servicios", "Informes", "Configuración"},
        )

    def test_services_menu_groups_services_and_schedule(self):
        self._assert_children(
            "menu_services",
            (
                "menu_extra_services_available",
                "menu_my_signups",
                "menu_extra_services",
                "menu_schedule",
            ),
        )

    def test_reports_menu_contains_summary_and_history(self):
        self.assertFalse(self._menu("menu_reports").action)
        self._assert_children("menu_reports", ("menu_extra_summary", "menu_history"))

    def test_configuration_menu_contains_agents(self):
        self._assert_children(
            "menu_configuration",
            (
                "menu_police_employees",
                "menu_police_categories",
                "menu_police_groups",
                "menu_working_schedules",
                "menu_service_types",
                "menu_shift_types",
                "menu_restrictions",
            ),
        )

    def test_coordinator_sees_agents_but_not_manager_configuration(self):
        coordinator = self.env["res.users"].create(
            {
                "name": "Coordinador Menú",
                "login": "coordinador.menu@example.com",
                "group_ids": [
                    (
                        6,
                        0,
                        [
                            self.env.ref(
                                "almonte_police_extra_services.group_police_extra_user"
                            ).id
                        ],
                    )
                ],
            }
        )
        visible_menu_ids = (
            self.env["ir.ui.menu"].with_user(coordinator)._visible_menu_ids()
        )
        self.assertIn(self._menu("menu_configuration").id, visible_menu_ids)
        self.assertIn(self._menu("menu_police_employees").id, visible_menu_ids)
        self.assertNotIn(self._menu("menu_service_types").id, visible_menu_ids)
        self.assertNotIn(self._menu("menu_police_categories").id, visible_menu_ids)
        self.assertNotIn(self._menu("menu_police_groups").id, visible_menu_ids)

    def test_schedule_action_opens_calendar_by_default(self):
        action = self.env.ref("almonte_police_extra_services.action_schedule")
        self.assertEqual(
            action.views,
            [
                (
                    self.env.ref(
                        "almonte_police_extra_services.view_schedule_assignment_calendar"
                    ).id,
                    "calendar",
                ),
                (
                    self.env.ref(
                        "almonte_police_extra_services.view_schedule_assignment_list"
                    ).id,
                    "list",
                ),
            ],
        )


class TestAlmontePoliceOrganization(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.category = cls.env["almonte.police.category"].create(
            {"name": "Categoría Test"}
        )
        cls.group = cls.env["almonte.police.group"].create({"name": "Grupo Test"})
        cls.employee = cls.env["hr.employee"].create(
            {
                "name": "Agente Organización Test",
                "police_dip": "DIP-9301",
                "police_category_id": cls.category.id,
                "police_group_id": cls.group.id,
            }
        )
        users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.manager_user = users.create(
            {
                "name": "Responsable Organización",
                "login": "responsable.organizacion@example.com",
                "group_ids": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref(
                                "almonte_police_extra_services."
                                "group_police_extra_manager"
                            ).id
                        ],
                    )
                ],
            }
        )
        cls.coordinator_user = users.create(
            {
                "name": "Coordinador Organización",
                "login": "coordinador.organizacion@example.com",
                "group_ids": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref(
                                "almonte_police_extra_services.group_police_extra_user"
                            ).id
                        ],
                    )
                ],
            }
        )

    def test_initial_categories_and_groups_are_loaded(self):
        categories = self.env["almonte.police.category"].search([])
        groups = self.env["almonte.police.group"].search([])
        self.assertTrue(
            {
                "Inspectora",
                "Subinspector",
                "Oficial Policía",
                "Oficial Policía Organizativo",
                "Policía",
            }
            <= set(categories.mapped("name"))
        )
        self.assertTrue(
            {"Jefatura", "Prommesas", "Grupo 1", "Grupo 6"}
            <= set(groups.mapped("name"))
        )

    def test_initial_employees_are_linked(self):
        employee = self.env.ref("almonte_police_extra_services.employee_police_101")
        self.assertEqual(
            employee.police_category_id,
            self.env.ref("almonte_police_extra_services.police_category_inspector"),
        )
        self.assertEqual(
            employee.police_group_id,
            self.env.ref("almonte_police_extra_services.police_group_jefatura"),
        )

    def test_employee_count(self):
        self.assertEqual(self.category.employee_count, 1)
        self.assertEqual(self.group.employee_count, 1)
        self.employee.police_group_id = False
        self.assertEqual(self.group.employee_count, 0)

    def test_category_name_must_be_unique(self):
        with (
            self.assertRaises(psycopg2.errors.UniqueViolation),
            mute_logger("odoo.sql_db"),
        ):
            self.env["almonte.police.category"].create({"name": "Categoría Test"})

    def test_group_name_must_be_unique(self):
        with (
            self.assertRaises(psycopg2.errors.UniqueViolation),
            mute_logger("odoo.sql_db"),
        ):
            self.env["almonte.police.group"].create({"name": "Grupo Test"})

    def test_used_category_and_group_cannot_be_deleted(self):
        with (
            self.assertRaises(psycopg2.errors.ForeignKeyViolation),
            mute_logger("odoo.sql_db"),
        ):
            self.category.unlink()
        with (
            self.assertRaises(psycopg2.errors.ForeignKeyViolation),
            mute_logger("odoo.sql_db"),
        ):
            self.group.unlink()

    def test_manager_can_maintain_categories_and_groups(self):
        category = (
            self.env["almonte.police.category"]
            .with_user(self.manager_user)
            .create({"name": "Nueva categoría"})
        )
        group = (
            self.env["almonte.police.group"]
            .with_user(self.manager_user)
            .create({"name": "Nuevo grupo"})
        )
        category.write({"name": "Categoría renombrada"})
        group.write({"active": False})
        self.assertEqual(category.name, "Categoría renombrada")
        self.assertFalse(group.active)
        category.unlink()
        self.assertFalse(category.exists())

    def test_coordinator_can_only_read_categories_and_groups(self):
        category_model = self.env["almonte.police.category"].with_user(
            self.coordinator_user
        )
        group_model = self.env["almonte.police.group"].with_user(self.coordinator_user)
        self.assertTrue(category_model.search([("id", "=", self.category.id)]))
        self.assertTrue(group_model.search([("id", "=", self.group.id)]))
        with self.assertRaises(AccessError):
            category_model.create({"name": "No permitida"})
        with self.assertRaises(AccessError):
            group_model.create({"name": "No permitido"})

    def test_employee_form_shows_police_section(self):
        arch = self.env["hr.employee"].get_view(
            self.env.ref("hr.view_employee_form").id, "form"
        )["arch"]
        police_section = etree.fromstring(arch).xpath("//group[@name='police_info']")
        self.assertEqual(len(police_section), 1)
        self.assertEqual(police_section[0].get("string"), "Policía Local")
        section_fields = set(police_section[0].xpath(".//field/@name"))
        self.assertEqual(
            section_fields,
            {
                "police_category_id",
                "police_group_id",
                "police_dip",
                "police_employee_id",
            },
        )


class TestAlmontePoliceSettlement(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.category = cls.env.ref(
            "almonte_police_extra_services.police_category_police"
        )
        cls.group = cls.env.ref("almonte_police_extra_services.police_group_group_3")
        employee_model = cls.env["hr.employee"]
        cls.agent_a = employee_model.create(
            {
                "name": "Agente Liquidación A",
                "police_employee_id": 9301,
                "identification_id": "52359118D",
                "police_category_id": cls.category.id,
                "police_group_id": cls.group.id,
            }
        )
        cls.agent_b = employee_model.create(
            {
                "name": "Agente Liquidación B",
                "police_employee_id": 9302,
                "police_category_id": cls.category.id,
            }
        )
        cls.agent_without_services = employee_model.create(
            {
                "name": "Agente sin servicios",
                "police_employee_id": 9303,
                "police_category_id": cls.category.id,
            }
        )
        cls.service_type = cls.env["almonte.police.service.type"].create(
            {"name": "Servicio liquidación", "code": "LIQ", "unit_price": 100}
        )
        cls._history(cls.agent_a, date(2026, 11, 3), 120, days=1)
        cls._history(cls.agent_a, date(2026, 11, 20), 80, days=0.5)
        cls._history(cls.agent_b, date(2026, 11, 30), 100, days=1)
        cls._history(cls.agent_b, date(2026, 12, 1), 100, days=1)
        cls.settlement_model = cls.env["almonte.police.extra.settlement"]

    @classmethod
    def _history(cls, employee, service_date, amount, days):
        service = cls.env["almonte.police.extra.service"].create(
            {
                "service_date": service_date,
                "required_shift_id": cls.env.ref(
                    "almonte_police_extra_services.shift_morning"
                ).id,
                "service_type_id": cls.service_type.id,
                "vacancies": 1,
            }
        )
        return cls.env["almonte.police.extra.service.history"].create(
            {
                "service_id": service.id,
                "employee_id": employee.id,
                "service_date": service_date,
                "service_type_id": cls.service_type.id,
                "amount": amount,
                "days": days,
            }
        )

    def _settlement(self, **values):
        defaults = {"period_type": "month", "month": "11", "year": 2026}
        return self.settlement_model.create(dict(defaults, **values))

    def test_monthly_settlement_only_includes_agents_with_services(self):
        settlement = self._settlement()
        self.assertEqual(settlement.date_from, date(2026, 11, 1))
        self.assertEqual(settlement.date_to, date(2026, 11, 30))
        self.assertEqual(
            settlement.name, "Liquidación Noviembre 2026 - Plantilla completa"
        )
        settlement.action_generate()
        self.assertEqual(settlement.line_ids.employee_id, self.agent_a | self.agent_b)
        self.assertNotIn(self.agent_without_services, settlement.line_ids.employee_id)
        line_a = settlement.line_ids.filtered(
            lambda line: line.employee_id == self.agent_a
        )
        self.assertEqual(line_a.identification_id, "52359118D")
        self.assertEqual(line_a.police_category_id, self.category)
        self.assertEqual(line_a.police_group_id, self.group)
        self.assertEqual(line_a.service_count, 2)
        self.assertEqual(line_a.total_days, 1.5)
        self.assertEqual(line_a.total_amount, 200)
        self.assertEqual(settlement.employee_count, 2)
        self.assertEqual(settlement.total_services, 3)
        self.assertEqual(settlement.total_days, 2.5)
        self.assertEqual(settlement.total_amount, 300)
        self.assertTrue(settlement.generated_on)

    def test_regenerate_replaces_lines(self):
        settlement = self._settlement()
        settlement.action_generate()
        self._history(self.agent_without_services, date(2026, 11, 15), 50, days=1)
        settlement.action_generate()
        self.assertEqual(settlement.employee_count, 3)
        self.assertEqual(settlement.total_amount, 350)

    def test_yearly_settlement_for_one_agent(self):
        settlement = self._settlement(
            period_type="year",
            month=False,
            scope="employee",
            employee_id=self.agent_b.id,
        )
        self.assertEqual(settlement.date_from, date(2026, 1, 1))
        self.assertEqual(settlement.date_to, date(2026, 12, 31))
        self.assertEqual(settlement.name, "Liquidación 2026 - Agente Liquidación B")
        settlement.action_generate()
        self.assertEqual(settlement.line_ids.employee_id, self.agent_b)
        self.assertEqual(settlement.line_ids.service_count, 2)
        self.assertEqual(settlement.total_amount, 200)

    def test_december_period_ends_on_last_day_of_year(self):
        settlement = self._settlement(month="12")
        self.assertEqual(settlement.date_to, date(2026, 12, 31))

    def test_invalid_period_or_scope_is_rejected(self):
        invalid_values = (
            {"month": False},
            {"year": 1999},
            {"scope": "employee"},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self._settlement(**values)

    @mute_logger("odoo.sql_db")
    def test_duplicate_period_and_scope_is_rejected(self):
        self._settlement()
        with self.assertRaises(psycopg2.errors.UniqueViolation):
            self._settlement()

    def test_confirmed_settlement_is_locked(self):
        settlement = self._settlement()
        settlement.action_confirm()
        self.assertEqual(settlement.state, "confirmed")
        self.assertEqual(settlement.employee_count, 2)
        with self.assertRaises(UserError):
            settlement.action_generate()
        with self.assertRaises(UserError):
            settlement.unlink()
        settlement.action_draft()
        self.assertEqual(settlement.state, "draft")
        settlement.unlink()
        self.assertFalse(settlement.exists())

    def test_onchanges_clear_irrelevant_fields(self):
        settlement = self.settlement_model.new(
            {
                "period_type": "year",
                "month": "5",
                "scope": "all",
                "employee_id": self.agent_a.id,
            }
        )
        settlement._onchange_period_type()
        settlement._onchange_scope()
        self.assertFalse(settlement.month)
        self.assertFalse(settlement.employee_id)

    def test_cron_generates_previous_month_settlement(self):
        with patch(
            "odoo.fields.Date.context_today",
            return_value=date(2026, 12, 1),
        ):
            settlement = self.settlement_model._cron_generate_previous_month()
            self.assertEqual((settlement.month, settlement.year), ("11", 2026))
            self.assertEqual(settlement.scope, "all")
            self.assertEqual(settlement.total_amount, 300)
            settlement.action_confirm()
            self._history(self.agent_without_services, date(2026, 11, 2), 50, 1)
            same = self.settlement_model._cron_generate_previous_month()
        self.assertEqual(same, settlement)
        self.assertEqual(settlement.total_amount, 300)
        cron = self.env.ref(
            "almonte_police_extra_services.ir_cron_generate_monthly_settlement"
        )
        self.assertEqual(cron.interval_type, "months")

    def test_pdf_and_excel_reports_render(self):
        settlement = self._settlement()
        settlement.action_generate()
        self.env.company.external_report_layout_id = self.env.ref(
            "web.external_layout_standard"
        )
        pdf_action = settlement.action_print_pdf()
        xlsx_action = settlement.action_export_xlsx()
        self.assertEqual(pdf_action["report_type"], "qweb-pdf")
        self.assertEqual(xlsx_action["report_type"], "xlsx")
        html = (
            self.env["ir.actions.report"]
            ._render_qweb_html(
                "almonte_police_extra_services.report_extra_settlement",
                settlement.ids,
            )[0]
            .decode()
        )
        self.assertIn("52359118D", html)
        self.assertIn("Agente Liquidación B", html)
        self.assertNotIn("Agente sin servicios", html)
        content, file_type = self.env["ir.actions.report"]._render(
            "almonte_police_extra_services.extra_settlement_xlsx", settlement.ids
        )
        self.assertEqual(file_type, "xlsx")
        self.assertTrue(content.startswith(b"PK"))

    def test_settlement_menu_is_under_reports(self):
        menu = self.env.ref("almonte_police_extra_services.menu_extra_settlements")
        self.assertEqual(
            menu.parent_id, self.env.ref("almonte_police_extra_services.menu_reports")
        )
