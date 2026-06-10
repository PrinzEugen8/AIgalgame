package com.aigalgame.demo

import org.junit.Assert.assertEquals
import org.junit.Test

class NetworkUrlTest {
    @Test
    fun backendUrlsDefaultToHttps() {
        assertEquals("https://example.com", normalizeBackendUrl("example.com/"))
        assertEquals("https://example.com", normalizeBackendUrl("https://example.com/"))
        assertEquals("https://example.com", normalizeBackendUrl("http://example.com/"))
    }
}
