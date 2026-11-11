package com.example.sample.auth

import com.example.sample.api.AuthApi

class SessionManager(
    private val store: TokenStore,
    private val api: AuthApi,
    private val clock: () -> Long,
) {
    private val refreshWindowMillis = 2 * 60 * 1000L
    private val maxRetries = 1

    fun validAccessToken(): String? {
        val token = store.load() ?: return null
        if (clock() < token.expiresAtMillis - refreshWindowMillis) {
            return token.access
        }
        return refresh(token)
    }

    private fun refresh(token: Token): String? {
        repeat(maxRetries) {
            val result = api.refresh(token.refresh)
            if (result != null) {
                store.save(result)
                return result.access
            }
        }
        logout()
        return null
    }

    fun logout() {
        store.clear()
    }
}
