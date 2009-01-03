#ifndef __UTILITY_H__
#define __UTILITY_H__

#include <glib-object.h>

#define UTILITY_TYPE_OBJECT              (utility_object_get_type ())
#define UTILITY_OBJECT(object)           (G_TYPE_CHECK_INSTANCE_CAST ((object), UTILITY_TYPE_OBJECT, UtilityObject))
#define UTILITY_IS_OBJECT(object)        (G_TYPE_CHECK_INSTANCE_TYPE ((object), UTILITY_TYPE_OBJECT))

typedef struct _UtilityObject          UtilityObject;
typedef struct _UtilityObjectClass     UtilityObjectClass;

struct _UtilityObject
{
  GObject parent_instance;
};

struct _UtilityObjectClass
{
  GObjectClass parent_class;
};

/* This one is similar to Pango.Glyph */
typedef guint32 UtilityGlyph;

typedef void (*UtilityFileFunc)(const char *path, gpointer user_data);

GType                 utility_object_get_type          (void) G_GNUC_CONST;
void                  utility_object_watch_dir         (UtilityObject *object,
                                                        const char *path,
                                                        UtilityFileFunc func,
                                                        gpointer user_data,
                                                        GDestroyNotify destroy);

typedef enum
{
  UTILITY_ENUM_A,
  UTILITY_ENUM_B,
  UTILITY_ENUM_C
} UtilityEnumType;

typedef enum
{
  UTILITY_FLAG_A,
  UTILITY_FLAG_B,
  UTILITY_FLAG_C
} UtilityFlagType;

typedef struct
{
  int field;
  guint bitfield1 : 3;
  guint bitfield2 : 2;
  guint8 data[16];
} UtilityStruct;

typedef union
{
  char *pointer;
  glong integer;
  double real;
} UtilityUnion;

void utility_dir_foreach (const char *path, UtilityFileFunc func, gpointer user_data);

#endif /* __UTILITY_H__ */
