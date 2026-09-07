from app.services.extraction.soft_block_detector import SoftBlockVerdict, detect_soft_block


def test_real_content_is_not_flagged():
    result = detect_soft_block(
        html="<html><body><article>" + "Real news content. " * 50 + "</article></body></html>",
        extracted_body="Real news content. " * 50,
        extraction_quality=0.8,
    )
    assert result.verdict == SoftBlockVerdict.REAL_CONTENT
    assert not result.is_blocked


def test_bot_challenge_marker_detected():
    result = detect_soft_block(
        html="<html><body>Checking your browser before accessing the site. Please complete the CAPTCHA.</body></html>",
        extracted_body="Checking your browser before accessing the site.",
        extraction_quality=0.3,
    )
    assert result.verdict == SoftBlockVerdict.BOT_CHALLENGE
    assert result.is_blocked


def test_password_field_flags_login_required():
    result = detect_soft_block(
        html='<html><body><form><input type="email"><input type="password"></form></body></html>',
        extracted_body="",
        extraction_quality=None,
    )
    assert result.verdict == SoftBlockVerdict.LOGIN_REQUIRED
    assert result.is_blocked


def test_login_marker_text_without_password_field_detected():
    result = detect_soft_block(
        html="<html><body>You must be logged in to view this page.</body></html>",
        extracted_body="You must be logged in to view this page.",
        extraction_quality=0.5,
    )
    assert result.verdict == SoftBlockVerdict.LOGIN_REQUIRED


def test_thin_body_flagged_as_thin_shell():
    result = detect_soft_block(
        html="<html><body><div id='root'></div></body></html>",
        extracted_body="",
        extraction_quality=None,
    )
    assert result.verdict == SoftBlockVerdict.THIN_SHELL
    assert result.is_blocked


def test_error_page_marker_with_thin_body_detected():
    result = detect_soft_block(
        html="<html><title>404 Not Found</title><body>The page you requested could not be found.</body></html>",
        extracted_body="The page you requested could not be found.",
        extraction_quality=0.2,
    )
    assert result.verdict == SoftBlockVerdict.ERROR_PAGE


def test_error_marker_with_substantial_real_body_not_flagged():
    # A real article that merely mentions "something went wrong" in a quote
    # should not be caught -- only a THIN body alongside the marker is.
    body = "The report describes how something went wrong during the launch. " * 10
    result = detect_soft_block(
        html=f"<html><body>{body}</body></html>", extracted_body=body, extraction_quality=0.6,
    )
    assert result.verdict == SoftBlockVerdict.REAL_CONTENT


def test_consent_wall_with_near_empty_body_detected():
    result = detect_soft_block(
        html="<html><body>We use cookies. Please accept all cookies to continue.</body></html>",
        extracted_body="We use cookies.",
        extraction_quality=0.2,
    )
    assert result.verdict == SoftBlockVerdict.CONSENT_WALL


def test_very_low_quality_thin_body_falls_back_to_generic_soft_block():
    result = detect_soft_block(html="<html><body>hmm</body></html>", extracted_body="hmm ok well", extraction_quality=0.05)
    assert result.verdict in (SoftBlockVerdict.SOFT_BLOCK, SoftBlockVerdict.THIN_SHELL)
    assert result.is_blocked
