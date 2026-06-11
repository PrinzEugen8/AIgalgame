package com.aigalgame.demo

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NotificationWorkerTest {
    @Test
    fun deliveredIsMarkedOnlyAfterNotificationIsPosted() {
        assertTrue(shouldMarkProactiveDelivered("pe_123", true))
        assertFalse(shouldMarkProactiveDelivered("pe_123", false))
        assertFalse(shouldMarkProactiveDelivered("", true))
    }
}
