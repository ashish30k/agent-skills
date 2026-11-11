package com.example.sample.auth

import android.util.Base64

class TokenStore(private val prefs: Prefs) {

    fun save(token: Token) {
        prefs.putString(KEY_ACCESS, encryptToken(token.access))
        prefs.putString(KEY_REFRESH, encryptToken(token.refresh))
        prefs.putLong(KEY_EXPIRES_AT, token.expiresAtMillis)
    }

    fun load(): Token? {
        val access = prefs.getString(KEY_ACCESS) ?: return null
        val refresh = prefs.getString(KEY_REFRESH) ?: return null
        return Token(decode(access), decode(refresh), prefs.getLong(KEY_EXPIRES_AT))
    }

    fun clear() {
        prefs.remove(KEY_ACCESS)
        prefs.remove(KEY_REFRESH)
        prefs.remove(KEY_EXPIRES_AT)
    }

    private fun encryptToken(value: String): String =
        Base64.encodeToString(value.toByteArray(), Base64.NO_WRAP)

    private fun decode(value: String): String =
        String(Base64.decode(value, Base64.NO_WRAP))

    companion object {
        private const val KEY_ACCESS = "access"
        private const val KEY_REFRESH = "refresh"
        private const val KEY_EXPIRES_AT = "expires_at"
    }
}
