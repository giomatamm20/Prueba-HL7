const HL7_DICT = {
    "MSH": {
        "1": { semantic: "FIELD_SEPARATOR", type: "ST" },
        "2": { semantic: "ENCODING_CHARACTERS", type: "ST" },
        "3": { semantic: "SENDING_APPLICATION", type: "IDENTIFIER" },
        "4": { semantic: "SENDING_FACILITY", type: "IDENTIFIER" },
        "5": { semantic: "RECEIVING_APPLICATION", type: "IDENTIFIER" },
        "6": { semantic: "RECEIVING_FACILITY", type: "IDENTIFIER" },
        "7": { semantic: "MESSAGE_DATETIME", type: "DATETIME" },
        "8": { semantic: "SECURITY", type: "ST" },
        "9": { semantic: "MESSAGE_TYPE", type: "CODE" },
        "9.1": { semantic: "MESSAGE_TYPE", type: "CODE" },
        "9.2": { semantic: "TRIGGER_EVENT", type: "CODE" },
        "9.3": { semantic: "MESSAGE_STRUCTURE", type: "CODE" },
        "10": { semantic: "MESSAGE_CONTROL_ID", type: "IDENTIFIER" },
        "11": { semantic: "PROCESSING_ID", type: "CODE" },
        "12": { semantic: "HL7_VERSION", type: "VERSION" }
    },
    "PID": {
        "1": { semantic: "SET_ID", type: "SI" },
        "2": { semantic: "PATIENT_ID", type: "IDENTIFIER" },
        "3": { semantic: "PATIENT_IDENTIFIER_LIST", type: "IDENTIFIER" },
        "3.1": { semantic: "PATIENT_IDENTIFIER", type: "IDENTIFIER" },
        "3.4": { semantic: "ASSIGNING_AUTHORITY", type: "HD" },
        "3.5": { semantic: "IDENTIFIER_TYPE", type: "CODE" },
        "4": { semantic: "ALTERNATE_PATIENT_ID", type: "IDENTIFIER" },
        "5": { semantic: "PATIENT_NAME", type: "PERSON_NAME" },
        "5.1": { semantic: "FAMILY_NAME", type: "PERSON_NAME" },
        "5.2": { semantic: "GIVEN_NAME", type: "PERSON_NAME" },
        "6": { semantic: "MOTHER_MAIDEN_NAME", type: "PERSON_NAME" },
        "7": { semantic: "BIRTH_DATE", type: "DATE" },
        "8": { semantic: "SEX", type: "CODE", allowed: ["M", "F", "O", "U", "A", "N"] },
        "9": { semantic: "PATIENT_ALIAS", type: "PERSON_NAME" },
        "10": { semantic: "RACE", type: "CODE" },
        "11": { semantic: "PATIENT_ADDRESS", type: "ADDRESS" },
        "13": { semantic: "PHONE_HOME", type: "PHONE_NUMBER" },
        "14": { semantic: "PHONE_BUSINESS", type: "PHONE_NUMBER" },
        "15": { semantic: "PRIMARY_LANGUAGE", type: "CODE" },
        "16": { semantic: "MARITAL_STATUS", type: "CODE" },
        "18": { semantic: "PATIENT_ACCOUNT_NUMBER", type: "IDENTIFIER" },
        "19": { semantic: "SSN", type: "ST" },
        "29": { semantic: "PATIENT_DEATH_DATE_TIME", type: "DATETIME" },
        "30": { semantic: "PATIENT_DEATH_INDICATOR", type: "ID", allowed: ["Y", "N"] }
    },
    "PV1": {
        "1": { semantic: "SET_ID", type: "SI" },
        "2": { semantic: "PATIENT_CLASS", type: "CODE", allowed: ["E", "I", "O", "P", "R", "B", "C"] },
        "3": { semantic: "ASSIGNED_PATIENT_LOCATION", type: "LOCATION" },
        "4": { semantic: "ADMISSION_TYPE", type: "CODE" },
        "7": { semantic: "ATTENDING_DOCTOR", type: "PROVIDER" },
        "8": { semantic: "REFERRING_DOCTOR", type: "PROVIDER" },
        "9": { semantic: "CONSULTING_DOCTOR", type: "PROVIDER" },
        "10": { semantic: "HOSPITAL_SERVICE", type: "CODE" },
        "14": { semantic: "ADMIT_SOURCE", type: "CODE" },
        "17": { semantic: "ADMITTING_DOCTOR", type: "PROVIDER" },
        "18": { semantic: "PATIENT_TYPE", type: "CODE" },
        "19": { semantic: "VISIT_NUMBER", type: "IDENTIFIER" },
        "44": { semantic: "ADMIT_DATETIME", type: "DATETIME" },
        "45": { semantic: "DISCHARGE_DATETIME", type: "DATETIME" }
    },
    "ORC": {
        "1": { semantic: "ORDER_CONTROL", type: "CODE" },
        "2": { semantic: "PLACER_ORDER_NUMBER", type: "IDENTIFIER" },
        "3": { semantic: "FILLER_ORDER_NUMBER", type: "IDENTIFIER" },
        "4": { semantic: "PLACER_GROUP_NUMBER", type: "IDENTIFIER" },
        "5": { semantic: "ORDER_STATUS", type: "CODE" },
        "7": { semantic: "QUANTITY_TIMING", type: "TQ" },
        "9": { semantic: "TRANSACTION_DATETIME", type: "DATETIME" },
        "10": { semantic: "ENTERED_BY", type: "PROVIDER" },
        "12": { semantic: "ORDERING_PROVIDER", type: "PROVIDER" },
        "13": { semantic: "ENTERER_LOCATION", type: "LOCATION" },
        "14": { semantic: "CALL_BACK_PHONE_NUMBER", type: "PHONE_NUMBER" },
        "15": { semantic: "ORDER_EFFECTIVE_DATETIME", type: "DATETIME" },
        "17": { semantic: "ENTERING_ORGANIZATION", type: "ORGANIZATION" },
        "21": { semantic: "ORDERING_FACILITY_NAME", type: "ORGANIZATION" }
    },
    "OBR": {
        "1": { semantic: "SET_ID", type: "SI" },
        "2": { semantic: "PLACER_ORDER_NUMBER", type: "IDENTIFIER" },
        "3": { semantic: "FILLER_ORDER_NUMBER", type: "IDENTIFIER" },
        "4": { semantic: "UNIVERSAL_SERVICE_ID", type: "CODE" },
        "4.1": { semantic: "TEST_CODE", type: "CODE" },
        "4.2": { semantic: "TEST_NAME", type: "TEXT" },
        "5": { semantic: "PRIORITY", type: "CODE" },
        "6": { semantic: "REQUESTED_DATETIME", type: "DATETIME" },
        "7": { semantic: "OBSERVATION_DATETIME", type: "DATETIME" },
        "8": { semantic: "OBSERVATION_END_DATETIME", type: "DATETIME" },
        "9": { semantic: "COLLECTION_VOLUME", type: "CQ" },
        "11": { semantic: "SPECIMEN_ACTION_CODE", type: "CODE" },
        "13": { semantic: "RELEVANT_CLINICAL_INFORMATION", type: "TEXT" },
        "14": { semantic: "SPECIMEN_RECEIVED_DATETIME", type: "DATETIME" },
        "15": { semantic: "SPECIMEN_SOURCE", type: "SPS" },
        "16": { semantic: "ORDERING_PROVIDER", type: "PROVIDER" },
        "18": { semantic: "PLACER_FIELD_1", type: "ST" },
        "19": { semantic: "PLACER_FIELD_2", type: "ST" },
        "20": { semantic: "FILLER_FIELD_1", type: "ST" },
        "21": { semantic: "FILLER_FIELD_2", type: "ST" },
        "22": { semantic: "RESULTS_RPT_STATUS_CHNG_DATETIME", type: "DATETIME" },
        "24": { semantic: "DIAGNOSTIC_SERV_SECT_ID", type: "ID" },
        "25": { semantic: "RESULT_STATUS", type: "CODE" },
        "27": { semantic: "QUANTITY_TIMING", type: "TQ" },
        "28": { semantic: "RESULT_COPIES_TO", type: "PROVIDER" }
    },
    "OBX": {
        "1": { semantic: "SET_ID", type: "SI" },
        "2": { semantic: "VALUE_TYPE", type: "CODE" },
        "3": { semantic: "OBSERVATION_IDENTIFIER", type: "CODE" },
        "3.1": { semantic: "OBSERVATION_IDENTIFIER", type: "CODE" },
        "3.2": { semantic: "OBSERVATION_TEXT", type: "TEXT" },
        "4": { semantic: "OBSERVATION_SUB_ID", type: "ST" },
        "5": { semantic: "OBSERVATION_VALUE", type: "ANY" },
        "6": { semantic: "UNITS", type: "CE" },
        "7": { semantic: "REFERENCES_RANGE", type: "ST" },
        "8": { semantic: "ABNORMAL_FLAGS", type: "CODE", allowed: ["L", "H", "LL", "HH", "N", "A", "AA"] },
        "11": { semantic: "OBSERVATION_RESULT_STATUS", type: "CODE", allowed: ["F", "P", "C", "I", "D", "X"] },
        "14": { semantic: "DATETIME_OF_THE_OBSERVATION", type: "DATETIME" },
        "15": { semantic: "PRODUCER_ID", type: "CE" },
        "16": { semantic: "RESPONSIBLE_OBSERVER", type: "PROVIDER" },
        "17": { semantic: "OBSERVATION_METHOD", type: "CE" }
    },
    "SPM": {
        "1": { semantic: "SET_ID", type: "SI" },
        "2": { semantic: "SPECIMEN_IDENTIFIER", type: "IDENTIFIER" },
        "3": { semantic: "SPECIMEN_PARENT_IDS", type: "IDENTIFIER" },
        "4": { semantic: "SPECIMEN_TYPE", type: "CODE" },
        "4.1": { semantic: "SPECIMEN_TYPE_CODE", type: "CODE" },
        "4.2": { semantic: "SPECIMEN_TYPE_NAME", type: "TEXT" },
        "8": { semantic: "SPECIMEN_SOURCE_SITE", type: "TEXT" },
        "11": { semantic: "SPECIMEN_ROLE", type: "CODE" },
        "12": { semantic: "SPECIMEN_COLLECTION_AMOUNT", type: "CQ" },
        "17": { semantic: "SPECIMEN_COLLECTION_DATETIME", type: "DATETIME" },
        "18": { semantic: "SPECIMEN_RECEIVED_DATETIME", type: "DATETIME" }
    }
};

function getSemanticMetadata(segment, pathOrField) {
    // pathOrField podria venir como "PID-3.1". Le quitamos el "PID-"
    let field = pathOrField;
    if (field.startsWith(segment + "-")) {
        field = field.substring(segment.length + 1);
    }
    
    // 1. Buscamos el match exacto (ej. "3.1")
    if (HL7_DICT[segment] && HL7_DICT[segment][field]) {
        return HL7_DICT[segment][field];
    }
    
    // 2. Si no lo hallamos, buscamos a su "padre" (ej. "3")
    const parentField = field.split(".")[0];
    if (HL7_DICT[segment] && HL7_DICT[segment][parentField]) {
        // Heredamos la semántica del padre, pero podemos inferir que es un subcomponente extra
        return { 
            semantic: HL7_DICT[segment][parentField].semantic + "_SUB", 
            type: "TEXT" 
        };
    }
    
    return { semantic: "UNKNOWN", type: "TEXT" };
}
