class AppException(Exception):
    status_code: int = 400
    code: str = "APP_ERROR"
    message: str = "An error occurred"

    def __init__(self, message: str | None = None):
        self.message = message or self.message
        super().__init__(self.message)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


class EmailAlreadyRegistered(AppException):
    status_code = 409
    code = "EMAIL_ALREADY_REGISTERED"
    message = "This email address is already registered"


class DisposableEmailNotAllowed(AppException):
    status_code = 400
    code = "DISPOSABLE_EMAIL_NOT_ALLOWED"
    message = "Disposable email addresses are not allowed"


class InvalidCredentials(AppException):
    status_code = 401
    code = "INVALID_CREDENTIALS"
    message = "Invalid email or password"


class EmailNotVerified(AppException):
    status_code = 403
    code = "EMAIL_NOT_VERIFIED"
    message = "Please verify your email address before signing in"


class AccountDisabled(AppException):
    status_code = 403
    code = "ACCOUNT_DISABLED"
    message = "This account has been disabled"


class UserNotFound(AppException):
    status_code = 404
    code = "USER_NOT_FOUND"
    message = "User not found"


class InvalidVerificationRequest(AppException):
    status_code = 400
    code = "INVALID_VERIFICATION_REQUEST"
    message = "Invalid verification request"


class InvalidVerificationCode(AppException):
    status_code = 400
    code = "INVALID_VERIFICATION_CODE"
    message = "Invalid verification code"


class VerificationCodeExpired(AppException):
    status_code = 400
    code = "VERIFICATION_CODE_EXPIRED"
    message = "This verification code has expired. Please request a new one"


class TooManyVerificationAttempts(AppException):
    status_code = 429
    code = "TOO_MANY_VERIFICATION_ATTEMPTS"
    message = "Too many failed attempts. Please request a new code"


class TooManyCodeRequests(AppException):
    status_code = 429
    code = "TOO_MANY_CODE_REQUESTS"
    message = "Too many codes requested. Please try again later"


class NoPendingEmailChange(AppException):
    status_code = 400
    code = "NO_PENDING_EMAIL_CHANGE"
    message = "No pending email change for this account"


class InvalidRefreshToken(AppException):
    status_code = 401
    code = "INVALID_REFRESH_TOKEN"
    message = "Invalid or expired refresh token"


class TokenReuseDetected(AppException):
    status_code = 401
    code = "TOKEN_REUSE_DETECTED"
    message = "Session invalidated for security reasons. Please sign in again"


class InvalidAccessToken(AppException):
    status_code = 401
    code = "INVALID_ACCESS_TOKEN"
    message = "Invalid or expired access token"


class InsufficientPermissions(AppException):
    status_code = 403
    code = "INSUFFICIENT_PERMISSIONS"
    message = "You do not have permission to perform this action"


class NoFieldsToUpdate(AppException):
    status_code = 400
    code = "NO_FIELDS_TO_UPDATE"
    message = "No fields to update"


class GoogleOnlyAccount(AppException):
    status_code = 400
    code = "GOOGLE_ONLY_ACCOUNT"
    message = "This account was created with Google and has no password"


class PasswordAlreadySet(AppException):
    status_code = 409
    code = "PASSWORD_ALREADY_SET"
    message = "This account already has a password"


class IncorrectCurrentPassword(AppException):
    status_code = 401
    code = "INCORRECT_CURRENT_PASSWORD"
    message = "The current password is incorrect"


class IncorrectPassword(AppException):
    status_code = 401
    code = "INCORRECT_PASSWORD"
    message = "The password is incorrect"


class SamePasswordAsBefore(AppException):
    status_code = 400
    code = "SAME_PASSWORD_AS_BEFORE"
    message = "The new password must be different from the current one"


class InvalidOrExpiredResetCode(AppException):
    status_code = 400
    code = "INVALID_OR_EXPIRED_RESET_CODE"
    message = "Invalid or expired reset code"


class InvalidResetCode(AppException):
    status_code = 400
    code = "INVALID_RESET_CODE"
    message = "Invalid reset code"


class ResetCodeExpired(AppException):
    status_code = 400
    code = "RESET_CODE_EXPIRED"
    message = "This reset code has expired. Please request a new one"


class NoEmailChangeCode(AppException):
    status_code = 400
    code = "NO_EMAIL_CHANGE_CODE"
    message = "No email change code has been requested"


class SameEmailAsCurrent(AppException):
    status_code = 400
    code = "SAME_EMAIL_AS_CURRENT"
    message = "The new email must be different from the current one"


class EmailAlreadyInUse(AppException):
    status_code = 409
    code = "EMAIL_ALREADY_IN_USE"
    message = "This email address is already in use"


class GoogleAuthUnavailable(AppException):
    status_code = 503
    code = "GOOGLE_AUTH_UNAVAILABLE"
    message = "Google sign-in is not configured on this server"


class GoogleAuthFailed(AppException):
    status_code = 401
    code = "GOOGLE_AUTH_FAILED"
    message = "Google sign-in could not be completed"


class GoogleEmailNotVerified(AppException):
    status_code = 403
    code = "GOOGLE_EMAIL_NOT_VERIFIED"
    message = "This Google account has no verified email address"


class CommitmentNotFound(AppException):
    status_code = 404
    code = "COMMITMENT_NOT_FOUND"
    message = "This commitment does not exist"


class OccurrenceNotFound(AppException):
    status_code = 404
    code = "OCCURRENCE_NOT_FOUND"
    message = "This due date does not exist"


class RestoreLimitReached(AppException):
    status_code = 409
    code = "RESTORE_LIMIT_REACHED"
    message = "Restoring these would take the account too far above its limit"

    def __init__(self, commitment_type: str, limit: int):
        super().__init__()
        self.commitment_type = commitment_type
        self.limit = limit

    def to_dict(self) -> dict:
        return {**super().to_dict(), "type": self.commitment_type, "limit": self.limit}


class CommitmentLimitReached(AppException):
    status_code = 409
    code = "COMMITMENT_LIMIT_REACHED"
    message = "This account already tracks the maximum number of commitments of this type"

    def __init__(self, commitment_type: str, limit: int):
        super().__init__()
        self.commitment_type = commitment_type
        self.limit = limit

    def to_dict(self) -> dict:
        # Le front compose son propre message : il lui faut le type pour choisir
        # le mot et la limite pour l'annoncer, sans la reecrire de son cote.
        return {**super().to_dict(), "type": self.commitment_type, "limit": self.limit}


class FuturePaymentDate(AppException):
    status_code = 400
    code = "FUTURE_PAYMENT_DATE"
    message = "The payment date cannot be in the future"


class InvalidDateRange(AppException):
    status_code = 400
    code = "INVALID_DATE_RANGE"
    message = "The end date must be on or after the start date"


class UnsupportedAvatarType(AppException):
    status_code = 415
    code = "UNSUPPORTED_AVATAR_TYPE"
    message = "Only JPEG and PNG images are allowed"


class AvatarTooLarge(AppException):
    status_code = 413
    code = "AVATAR_TOO_LARGE"
    message = "The image must not exceed 9 MB"


class AvatarUploadFailed(AppException):
    status_code = 502
    code = "AVATAR_UPLOAD_FAILED"
    message = "The image could not be stored, please try again"


class StorageUnavailable(AppException):
    status_code = 503
    code = "STORAGE_UNAVAILABLE"
    message = "File storage is not configured"


class InvalidDeletionConfirmation(AppException):
    status_code = 400
    code = "INVALID_DELETION_CONFIRMATION"
    message = "The confirmation does not match"


class GoogleAccountAlreadyLinked(AppException):
    status_code = 409
    code = "GOOGLE_ACCOUNT_ALREADY_LINKED"
    message = "This email address is already linked to a different Google account"


class PushNotConfigured(AppException):
    status_code = 503
    code = "PUSH_NOT_CONFIGURED"
    message = "Push notifications are not available on this server"


class PushEndpointRefused(AppException):
    status_code = 400
    code = "PUSH_ENDPOINT_REFUSED"
    message = "This push address does not belong to a known push service"


class PushSubscriptionGone(AppException):
    status_code = 410
    code = "PUSH_SUBSCRIPTION_GONE"
    message = "This device is no longer reachable"
