import io
import json
import logging
import os
import random
import re
import sys
import tempfile
import time
from base64 import b64decode
from dataclasses import dataclass, field
from datetime import datetime as dt
from enum import Enum
from json.decoder import JSONDecodeError
from typing import Any, Dict, Optional

import backoff
import requests
from anticaptchaofficial.imagecaptcha import imagecaptcha
from anticaptchaofficial.recaptchav3proxyless import recaptchaV3Proxyless
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.wait import WebDriverWait

from .speaker import new_speaker

__all__ = [
    "try_cita",
    "start_with",
    "init_wedriver",
    "CustomerProfile",
    "DocType",
    "OperationType",
    "Office",
    "Province",
]

CYCLES = 144
REFRESH_PAGE_CYCLES = 12

DELAY = 30  # timeout for page load

speaker = new_speaker()


class DocType(str, Enum):
    DNI = "dni"
    NIE = "nie"
    PASSPORT = "passport"


class OperationType(str, Enum):
    AUTORIZACION_DE_REGRESO = "20"  # POLICIA-AUTORIZACIÓN DE REGRESO
    BREXIT = "4094"  # POLICÍA-EXP.TARJETA ASOCIADA AL ACUERDO DE RETIRADA CIUDADANOS BRITÁNICOS Y SUS FAMILIARES (BREXIT)
    CARTA_INVITACION = "4037"  # POLICIA-CARTA DE INVITACIÓN
    CERTIFICADOS_NIE = "4096"  # POLICIA-CERTIFICADOS Y ASIGNACION NIE
    CERTIFICADOS_NIE_NO_COMUN = "4079"  # POLICIA-CERTIFICADOS Y ASIGNACION NIE (NO COMUNITARIOS)
    CERTIFICADOS_RESIDENCIA = "4049"  # POLICIA-CERTIFICADOS (DE RESIDENCIA, DE NO RESIDENCIA Y DE CONCORDANCIA) #fmt: off
    CERTIFICADOS_UE = "4038"  # POLICIA-CERTIFICADO DE REGISTRO DE CIUDADANO DE LA U.E.
    RECOGIDA_DE_TARJETA = "4036"  # POLICIA - RECOGIDA DE TARJETA DE IDENTIDAD DE EXTRANJERO (TIE)
    SOLICITUD_ASILO = "4078"  # POLICIA - SOLICITUD ASILO
    TOMA_HUELLAS = "4010"  # POLICIA-TOMA DE HUELLAS (EXPEDICIÓN DE TARJETA) Y RENOVACIÓN DE TARJETA DE LARGA DURACIÓN
    ASIGNACION_NIE = "4031"  # Asignación de N.I.E.
    FINGERP_RINT = "4047"  # POLICÍA-EXPEDICIÓN DE TARJETAS CUYA AUTORIZACIÓN RESUELVE LA DIRECCIÓN GENERAL DE MIGRACIONES


class Office(str, Enum):
    # Barcelona
    BADALONA = "18"  # CNP-COMISARIA BADALONA, AVDA. DELS VENTS (9)
    BARCELONA = "16"  # CNP - RAMBLA GUIPUSCOA 74, RAMBLA GUIPUSCOA (74)
    BARCELONA_MALLORCA = "14"  # CNP MALLORCA-GRANADOS, MALLORCA (213)
    CASTELLDEFELS = "19"  # CNP-COMISARIA CASTELLDEFELS, PLAÇA DE L`ESPERANTO (4)
    CERDANYOLA = "20"  # CNP-COMISARIA CERDANYOLA DEL VALLES, VERGE DE LES FEIXES (4)
    CORNELLA = "21"  # CNP-COMISARIA CORNELLA DE LLOBREGAT, AV. SANT ILDEFONS, S/N
    ELPRAT = "23"  # CNP-COMISARIA EL PRAT DE LLOBREGAT, CENTRE (4)
    GRANOLLERS = "28"  # CNP-COMISARIA GRANOLLERS, RICOMA (65)
    HOSPITALET = "17"  # CNP-COMISARIA L`HOSPITALET DE LLOBREGAT, Rbla. Just Oliveres (43)
    IGUALADA = "26"  # CNP-COMISARIA IGUALADA, PRAT DE LA RIBA (13)
    MANRESA = "38"  # CNP-COMISARIA MANRESA, SOLER I MARCH (5)
    MATARO = "27"  # CNP-COMISARIA MATARO, AV. GATASSA (15)
    MONTCADA = "31"  # CNP-COMISARIA MONTCADA I REIXAC, MAJOR (38)
    RIPOLLET = "32"  # CNP-COMISARIA RIPOLLET, TAMARIT (78)
    RUBI = "29"  # CNP-COMISARIA RUBI, TERRASSA (16)
    SABADELL = "30"  # CNP-COMISARIA SABADELL, BATLLEVELL (115)
    SANTACOLOMA = "35"  # CNP-COMISARIA SANTA COLOMA DE GRAMENET, IRLANDA (67)
    SANTADRIA = "33"  # CNP-COMISARIA SANT ADRIA DEL BESOS, AV. JOAN XXIII (2)
    SANTBOI = "24"  # CNP-COMISARIA SANT BOI DE LLOBREGAT, RIERA BASTÉ (43)
    SANTCUGAT = "34"  # CNP-COMISARIA SANT CUGAT DEL VALLES, VALLES (1)
    SANTFELIU = "22"  # CNP-COMISARIA SANT FELIU DE LLOBREGAT, CARRERETES (9)
    TERRASSA = "36"  # CNP-COMISARIA TERRASSA, BALDRICH (13)
    VIC = "37"  # CNP-COMISARIA VIC, BISBE MORGADES (4)
    VILADECANS = "25"  # CNP-COMISARIA VILADECANS, AVDA. BALLESTER (2)
    VILAFRANCA = "46"  # CNP COMISARIA VILAFRANCA DEL PENEDES, Avinguda Ronda del Mar, 109
    VILANOVA = "39"  # CNP-COMISARIA VILANOVA I LA GELTRU, VAPOR (19)

    # Tenerife
    OUE_SANTA_CRUZ = "1"  # 1 OUE SANTA CRUZ DE TENERIFE,  C/LA MARINA, 20
    PLAYA_AMERICAS = "2"  # CNP-Playa de las Américas, Av. de los Pueblos, 2
    PUERTO_CRUZ = "3"  # CNP-Puerto de la Cruz/Los Realejos, Av. del Campo y Llarena, 3


class Province(str, Enum):
    A_CORUÑA = "15"
    ALBACETE = "2"
    ALICANTE = "3"
    ALMERÍA = "4"
    ARABA = "1"
    ASTURIAS = "33"
    ÁVILA = "5"
    BADAJOZ = "6"
    BARCELONA = "8"
    BIZKAIA = "48"
    BURGOS = "9"
    CÁCERES = "10"
    CÁDIZ = "11"
    CANTABRIA = "39"
    CASTELLÓN = "12"
    CEUTA = "51"
    CIUDAD_REAL = "13"
    CÓRDOBA = "14"
    CUENCA = "16"
    GIPUZKOA = "20"
    GIRONA = "17"
    GRANADA = "18"
    GUADALAJARA = "19"
    HUELVA = "21"
    HUESCA = "22"
    ILLES_BALEARS = "7"
    JAÉN = "23"
    LA_RIOJA = "26"
    LAS_PALMAS = "35"
    LEÓN = "24"
    LLEIDA = "25"
    LUGO = "27"
    MADRID = "28"
    MÁLAGA = "29"
    MELILLA = "52"
    MURCIA = "30"
    NAVARRA = "31"
    ORENSE = "32"
    PALENCIA = "34"
    PONTEVEDRA = "36"
    SALAMANCA = "37"
    S_CRUZ_TENERIFE = "38"
    SEGOVIA = "40"
    SEVILLA = "41"
    SORIA = "42"
    TARRAGONA = "43"
    TERUEL = "44"
    TOLEDO = "45"
    VALENCIA = "46"
    VALLADOLID = "47"
    ZAMORA = "49"
    ZARAGOZA = "50"


@dataclass
class CustomerProfile:
    name: str
    doc_type: DocType
    doc_value: str  # Passport? "123123123"; Nie? "Y1111111M"
    phone: str
    email: str
    province: Province = Province.BARCELONA
    operation_code: OperationType = OperationType.TOMA_HUELLAS
    country: str = "RUSIA"
    year_of_birth: Optional[str] = None
    offices: Optional[list] = field(default_factory=list)
    except_offices: Optional[list] = field(default_factory=list)

    anticaptcha_api_key: Optional[str] = None
    auto_captcha: bool = True
    auto_office: bool = True
    chrome_driver_path: str = "/usr/local/bin/chromedriver"
    chrome_profile_name: Optional[str] = None
    chrome_profile_path: Optional[str] = None
    min_date: Optional[str] = None  # "dd/mm/yyyy"
    max_date: Optional[str] = None  # "dd/mm/yyyy"
    min_time: Optional[str] = None  # "hh:mm"
    max_time: Optional[str] = None  # "hh:mm"
    save_artifacts: bool = False
    sms_webhook_token: Optional[str] = None
    wait_exact_time: Optional[list] = None  # [[minute, second]]
    reason_or_type: str = "solicitud de asilo"

    # Internals
    bot_result: bool = False
    first_load: Optional[bool] = True  # Wait more on the first load to cache stuff
    log_settings: Optional[dict] = field(default_factory=lambda: {"stream": sys.stdout})
    recaptcha_solver: Any = None
    image_captcha_solver: Any = None
    current_solver: Any = None

    def __post_init__(self):
        if self.operation_code == OperationType.RECOGIDA_DE_TARJETA:
            assert len(self.offices) == 1, "Indicate the office where you need to pick up the card"


def init_wedriver(context: CustomerProfile):
    options = webdriver.ChromeOptions()

    if context.chrome_profile_path:
        options.add_argument(f"user-data-dir={context.chrome_profile_path}")
    if context.chrome_profile_name:
        options.add_argument(f"profile-directory={context.chrome_profile_name}")

    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.5005.63 Safari/537.36"

    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--ignore-ssl-errors")
    options.add_argument("--disable-gpu")

    settings = {
        "recentDestinations": [{"id": "Save as PDF", "origin": "local", "account": ""}],
        "selectedDestinationId": "Save as PDF",
        "version": 2,
    }
    prefs = {
        "printing.print_preview_sticky_settings.appState": json.dumps(settings),
        "download.default_directory": os.getcwd(),
    }
    options.add_experimental_option("prefs", prefs)
    options.add_argument("--kiosk-printing")

    browser = webdriver.Chrome(context.chrome_driver_path, options=options)
    browser.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    browser.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": ua})

    return browser


def try_cita(context: CustomerProfile, cycles: int = CYCLES):
    driver = init_wedriver(context)
    start_with(driver, context, cycles)


def start_with(driver: webdriver, context: CustomerProfile, cycles: int = CYCLES):
    logging.basicConfig(
        format="%(asctime)s - %(message)s", level=logging.INFO, **context.log_settings  # type: ignore
    )
    if context.sms_webhook_token:
        delete_message(context.sms_webhook_token)

    operation_category = "icpplus"
    operation_param = "tramiteGrupo[1]"

    if context.province == Province.BARCELONA:
        operation_category = "icpplustieb"
        operation_param = "tramiteGrupo[0]"
    elif context.province in [
        Province.ALICANTE,
        Province.ILLES_BALEARS,
        Province.LAS_PALMAS,
        Province.S_CRUZ_TENERIFE,
    ]:
        operation_category = "icpco"
    elif context.province == Province.MADRID:
        operation_category = "icpplustiem"
    elif context.province == Province.MÁLAGA:
        operation_category = "icpco"
        operation_param = "tramiteGrupo[0]"
    elif context.province in [
        Province.MELILLA,
        Province.SEVILLA,
    ]:
        operation_param = "tramiteGrupo[0]"

    fast_forward_url = "https://icp.administracionelectronica.gob.es/{}/citar?p={}".format(
        operation_category, context.province
    )
    fast_forward_url2 = "https://icp.administracionelectronica.gob.es/{}/acInfo?{}={}".format(
        operation_category, operation_param, context.operation_code
    )

    success = False
    result = False
    for i in range(cycles):
        try:
            logging.info(f"\033[33m[Attempt {i + 1}/{cycles}]\033[0m")
            result = cycle_cita(driver, context, fast_forward_url, fast_forward_url2)
        except KeyboardInterrupt:
            raise
        except TimeoutException:
            logging.error("Timeout exception")
        except Exception as e:
            logging.error(f"SMTH BROKEN: {e}")
            continue

        if result:
            success = True
            logging.info("WIN")
            break

    if not success:
        logging.error("FAIL")
        speaker.say("FAIL")
        driver.quit()


def toma_huellas_step2(driver: webdriver, context: CustomerProfile):
    try:
        WebDriverWait(driver, DELAY).until(EC.presence_of_element_located((By.ID, "txtPaisNac")))
    except TimeoutException:
        logging.error("Timed out waiting for form to load")
        return None

    # Select country
    select = Select(driver.find_element(By.ID, "txtPaisNac"))
    select.select_by_visible_text(context.country)

    # Select doc type
    if context.doc_type == DocType.PASSPORT:
        driver.find_element(By.ID, "rdbTipoDocPas").send_keys(Keys.SPACE)
    elif context.doc_type == DocType.NIE:
        driver.find_element(By.ID, "rdbTipoDocNie").send_keys(Keys.SPACE)

    # Enter doc number and name
    element = driver.find_element(By.ID, "txtIdCitado")
    element.send_keys(context.doc_value, Keys.TAB, context.name)

    return True


def recogida_de_tarjeta_step2(driver: webdriver, context: CustomerProfile):
    try:
        WebDriverWait(driver, DELAY).until(EC.presence_of_element_located((By.ID, "txtIdCitado")))
    except TimeoutException:
        logging.error("Timed out waiting for form to load")
        return None

    # Select doc type
    if context.doc_type == DocType.PASSPORT:
        driver.find_element(By.ID, "rdbTipoDocPas").send_keys(Keys.SPACE)
    elif context.doc_type == DocType.NIE:
        driver.find_element(By.ID, "rdbTipoDocNie").send_keys(Keys.SPACE)

    # Enter doc number and name
    element = driver.find_element(By.ID, "txtIdCitado")
    element.send_keys(context.doc_value, Keys.TAB, context.name)

    return True


def solicitud_asilo_step2(driver: webdriver, context: CustomerProfile):
    try:
        WebDriverWait(driver, DELAY).until(EC.presence_of_element_located((By.ID, "txtIdCitado")))
    except TimeoutException:
        logging.error("Timed out waiting for form to load")
        return None

    # Select doc type
    if context.doc_type == DocType.PASSPORT:
        driver.find_element(By.ID, "rdbTipoDocPas").send_keys(Keys.SPACE)
    elif context.doc_type == DocType.NIE:
        driver.find_element(By.ID, "rdbTipoDocNie").send_keys(Keys.SPACE)

    # Enter doc number and name
    element = driver.find_element(By.ID, "txtIdCitado")
    element.send_keys(context.doc_value, Keys.TAB, context.name, Keys.TAB, context.year_of_birth)

    # Select country
    select = Select(driver.find_element(By.ID, "txtPaisNac"))
    select.select_by_visible_text(context.country)

    return True


def brexit_step2(driver: webdriver, context: CustomerProfile):
    try:
        WebDriverWait(driver, DELAY).until(EC.presence_of_element_located((By.ID, "txtIdCitado")))
    except TimeoutException:
        logging.error("Timed out waiting for form to load")
        return None

    # Select doc type
    if context.doc_type == DocType.PASSPORT:
        driver.find_element(By.ID, "rdbTipoDocPas").send_keys(Keys.SPACE)
    elif context.doc_type == DocType.NIE:
        driver.find_element(By.ID, "rdbTipoDocNie").send_keys(Keys.SPACE)

    # Enter doc number and name
    element = driver.find_element(By.ID, "txtIdCitado")
    element.send_keys(context.doc_value, Keys.TAB, context.name)

    return True


def carta_invitacion_step2(driver: webdriver, context: CustomerProfile):
    try:
        WebDriverWait(driver, DELAY).until(EC.presence_of_element_located((By.ID, "txtIdCitado")))
    except TimeoutException:
        logging.error("Timed out waiting for form to load")
        return None

    # Select doc type
    if context.doc_type == DocType.PASSPORT:
        driver.find_element(By.ID, "rdbTipoDocPas").send_keys(Keys.SPACE)
    elif context.doc_type == DocType.DNI:
        driver.find_element(By.ID, "rdbTipoDocDni").send_keys(Keys.SPACE)
    elif context.doc_type == DocType.NIE:
        driver.find_element(By.ID, "rdbTipoDocNie").send_keys(Keys.SPACE)

    # Enter doc number and name
    element = driver.find_element(By.ID, "txtIdCitado")
    element.send_keys(context.doc_value, Keys.TAB, context.name)

    return True


def certificados_step2(driver: webdriver, context: CustomerProfile):
    try:
        WebDriverWait(driver, DELAY).until(EC.presence_of_element_located((By.ID, "txtIdCitado")))
    except TimeoutException:
        logging.error("Timed out waiting for form to load")
        return None

    # Select doc type
    if context.doc_type == DocType.PASSPORT:
        driver.find_element(By.ID, "rdbTipoDocPas").send_keys(Keys.SPACE)
    elif context.doc_type == DocType.NIE:
        driver.find_element(By.ID, "rdbTipoDocNie").send_keys(Keys.SPACE)
    elif context.doc_type == DocType.DNI:
        driver.find_element(By.ID, "rdbTipoDocDni").send_keys(Keys.SPACE)

    # Enter doc number and name
    element = driver.find_element(By.ID, "txtIdCitado")
    element.send_keys(context.doc_value, Keys.TAB, context.name)

    return True


def autorizacion_de_regreso_step2(driver: webdriver, context: CustomerProfile):
    try:
        WebDriverWait(driver, DELAY).until(EC.presence_of_element_located((By.ID, "txtIdCitado")))
    except TimeoutException:
        logging.error("Timed out waiting for form to load")
        return None

    # Select doc type
    if context.doc_type == DocType.PASSPORT:
        driver.find_element(By.ID, "rdbTipoDocPas").send_keys(Keys.SPACE)
    elif context.doc_type == DocType.NIE:
        driver.find_element(By.ID, "rdbTipoDocNie").send_keys(Keys.SPACE)

    # Enter doc number and name
    element = driver.find_element(By.ID, "txtIdCitado")
    element.send_keys(context.doc_value, Keys.TAB, context.name)

    return True


def asignacion_nie_step2(driver: webdriver, context: CustomerProfile):
    try:
        WebDriverWait(driver, DELAY).until(EC.presence_of_element_located((By.ID, "txtIdCitado")))
    except TimeoutException:
        logging.error("Timed out waiting for form to load")
        return None

    # Select doc type
    if context.doc_type == DocType.PASSPORT:
        option = driver.find_element(By.ID, "rdbTipoDocPas")
        if option:
            option.send_keys(Keys.SPACE)

    # Enter doc number, name and year of birth
    element = driver.find_element(By.ID, "txtIdCitado")
    element.send_keys(context.doc_value, Keys.TAB, context.name, Keys.TAB, context.year_of_birth)

    # Select country
    select = Select(driver.find_element(By.ID, "txtPaisNac"))
    select.select_by_visible_text(context.country)

    return True


def wait_exact_time(driver: webdriver, context: CustomerProfile):
    if context.wait_exact_time:
        WebDriverWait(driver, 1200).until(
            lambda _x: [dt.now().minute, dt.now().second] in context.wait_exact_time
        )


def body_text(driver: webdriver):
    try:
        WebDriverWait(driver, DELAY).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        return driver.find_element(By.TAG_NAME, "body").text
    except TimeoutException:
        logging.info("Timed out waiting for body to load")
        return ""


def process_captcha(driver: webdriver, context: CustomerProfile):
    if context.auto_captcha:
        if not context.anticaptcha_api_key:
            logging.error("Anticaptcha API key is empty")
            return None

        if len(driver.find_elements(By.ID, "reCAPTCHA_site_key")) > 0:
            captcha_result = solve_recaptcha(driver, context)
        elif len(driver.find_elements(By.CSS_SELECTOR, "img.img-thumbnail")) > 0:
            captcha_result = solve_image_captcha(driver, context)
        else:
            captcha_result = True

        if not captcha_result:
            return None

    else:
        logging.info(
            "HEY, DO SOMETHING HUMANE TO TRICK THE CAPTCHA (select text, move cursor etc.) and press ENTER"
        )
        for i in range(10):
            speaker.say("ALARM")
        input()

    return True


def solve_recaptcha(driver: webdriver, context: CustomerProfile):
    if not context.recaptcha_solver:
        site_key = driver.find_element(By.ID, "reCAPTCHA_site_key").get_attribute("value")
        page_action = driver.find_element(By.ID, "action").get_attribute("value")
        logging.info("Anticaptcha: site key: " + site_key)
        logging.info("Anticaptcha: action: " + page_action)

        context.recaptcha_solver = recaptchaV3Proxyless()
        context.recaptcha_solver.set_verbose(1)
        context.recaptcha_solver.set_key(context.anticaptcha_api_key)
        context.recaptcha_solver.set_website_url("https://icp.administracionelectronica.gob.es")
        context.recaptcha_solver.set_website_key(site_key)
        context.recaptcha_solver.set_page_action(page_action)
        context.recaptcha_solver.set_min_score(0.9)

    context.current_solver = type(context.recaptcha_solver)

    g_response = context.recaptcha_solver.solve_and_return_solution()
    if g_response != 0:
        logging.info("Anticaptcha: g-response: " + g_response)
        driver.execute_script(
            f"document.getElementById('g-recaptcha-response').value = '{g_response}'"
        )
        return True
    else:
        logging.error("Anticaptcha: " + context.recaptcha_solver.err_string)
        return None


def solve_image_captcha(driver: webdriver, context: CustomerProfile):
    if not context.image_c
