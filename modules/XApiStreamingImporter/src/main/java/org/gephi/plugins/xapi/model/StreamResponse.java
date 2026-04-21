package org.gephi.plugins.xapi.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonIgnoreProperties(ignoreUnknown = true)
public class StreamResponse {

    @JsonProperty("data")
    public Tweet data;

    @JsonProperty("includes")
    public Includes includes;
}
