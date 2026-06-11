package com.aigalgame.demo

import org.junit.Assert.assertEquals
import org.junit.Test

class NetworkUrlTest {
    @Test
    fun backendUrlsDefaultToHttpsWhenSchemeIsMissing() {
        assertEquals("https://example.com", normalizeBackendUrl("example.com/"))
        assertEquals("https://example.com", normalizeBackendUrl("https://example.com/"))
    }

    @Test
    fun backendUrlsPreserveExplicitHttpForLanTunnels() {
        assertEquals("http://example.com", normalizeBackendUrl("http://example.com/"))
        assertEquals("http://192.168.31.235:8765", normalizeBackendUrl(" http://192.168.31.235:8765/ "))
    }
}
