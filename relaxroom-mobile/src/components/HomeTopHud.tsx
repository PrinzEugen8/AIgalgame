import React, {useEffect, useState} from 'react';
import {Pressable, StyleSheet, Text, View} from 'react-native';
import {relationshipDay} from '../api/relaxroom';
import {colors} from '../theme/colors';
import {SettingsIcon, UsersIcon} from './icons';
import type {HomeBootstrap} from '../types/dialogue';

type Props = {
  home?: HomeBootstrap;
  onOpenMoments: () => void;
  onOpenSettings: () => void;
};

function formatTime(date: Date) {
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function formatDate(date: Date) {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${month} / ${day}`;
}

export function HomeTopHud({home, onOpenMoments, onOpenSettings}: Props) {
  const [now, setNow] = useState(new Date());
  const day = relationshipDay(home?.relationship_start_date);

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <View style={styles.container} pointerEvents="box-none">
      <View style={styles.left} pointerEvents="none">
        <Text style={styles.time}>{formatTime(now)}</Text>
        <Text style={styles.date}>{formatDate(now)}</Text>
        <Text style={styles.day}>第 {day} 天</Text>
        {home?.daily_tip ? (
          <Text style={styles.tip} numberOfLines={1}>
            {home.daily_tip}
          </Text>
        ) : null}
      </View>

      <View style={styles.right} pointerEvents="box-none">
        <Pressable style={styles.iconButton} onPress={onOpenMoments}>
          <UsersIcon />
        </Pressable>
        <Pressable style={styles.iconButton} onPress={onOpenSettings}>
          <SettingsIcon />
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    paddingHorizontal: 16,
    paddingVertical: 8,
    zIndex: 20,
    elevation: 20,
  },
  left: {
    gap: 2,
    flex: 1,
    paddingRight: 12,
  },
  time: {
    color: colors.textPrimary,
    fontSize: 36,
    fontWeight: '300',
    letterSpacing: 1,
  },
  date: {
    color: colors.textSecondary,
    fontSize: 14,
    marginTop: 2,
  },
  day: {
    color: colors.textSecondary,
    fontSize: 13,
    marginTop: 2,
  },
  tip: {
    color: colors.textMuted,
    fontSize: 11,
    marginTop: 4,
    maxWidth: 220,
  },
  right: {
    flexDirection: 'row',
    gap: 8,
    paddingTop: 4,
  },
  iconButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: colors.panelBg,
    borderWidth: 1,
    borderColor: colors.panelBorder,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
